import logging
from pathlib import Path

import numpy as np
from matplotlib import cm
from OpenGL.GL import GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT, GL_DEPTH_TEST
from PySide6 import QtCore
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtOpenGL import QOpenGLDebugLogger, QOpenGLFramebufferObject
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from ...analysis.shape_analysis import CrystalShape
from ...fileio.xyz_file import read_XYZ
from .axes_renderer import AxesRenderer
from .camera import Camera
from .point_cloud_renderer import SimplePointRenderer
from .sphere_renderer import SphereRenderer
from .mesh_renderer import MeshRenderer
from .line_renderer import LineRenderer
from ..widgets.overlay_widget import TransparentOverlay
import trimesh
from scipy.spatial import ConvexHull


logger = logging.getLogger("CA:OpenGL")


class VisualisationWidget(QOpenGLWidget):
    style = "Spheres"
    show_mesh_edges = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.rightMouseButtonPressed = False
        self.lastMousePosition = QtCore.QPoint()
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.camera = Camera()
        self.restrict_axis = None
        self.geom = self.geometry()
        self.centre = self.geom.center()
        print(self.geom)
        print(self.geom.getRect())

        self.xyz_path_list = []
        self.sim_num = 0
        self.point_cloud_renderer = None
        self.sphere_renderer = None
        self.mesh_renderer = None
        self.axes_renderer = None

        self.xyz = None
        self.movie = None
        # self.object = 0

        self.colormap = "Viridis"
        self.color_by = "Layer"

        self.viewInitialized = False
        self.point_size = 6.0
        self.point_type = "Point"
        self.backgroundColor = QColor(Qt.white)

        self.overlay = TransparentOverlay(self)
        self.overlay.setGeometry(self.geometry())
        self.overlay.showIcon()

        self.lattice_parameters = None

        self.availableColormaps = {
            "Viridis": cm.viridis,
            "Cividis": cm.cividis,
            "Plasma": cm.plasma,
            "Inferno": cm.inferno,
            "Magma": cm.magma,
            "Twilight": cm.twilight,
            "HSV": cm.hsv,
        }

        self.columnLabelToIndex = {
            "Atom/Molecule Type": 0,
            "Atom/Molecule Number": 1,
            "Layer": 2,
            "Single Colour": -1,
            "Site Number": 6,
            "Particle Energy": 7,
        }

        self.availableColumns = {}

    def pass_XYZ(self, xyz):
        self.xyz = xyz
        logger.debug("XYZ coordinates passed on OpenGL widget")

    def pass_XYZ_list(self, xyz_path_list):
        self.xyz_path_list = xyz_path_list
        logger.info("XYZ file paths (list) passed to OpenGL widget")

    def get_XYZ_from_list(self, value):
        if self.sim_num != value:
            self.sim_num = value
            self.xyz, self.movie = read_XYZ(self.xyz_path_list[value])
            self.initGeometry()

            self.update()

    def saveRenderDialog(self):
        # Create a list of options for the dropdown menu
        options = ["1x", "2x", "4x"]

        # Show the input dialog and get the index of the selected item
        resolution, ok = QInputDialog.getItem(
            self, "Select Resolution", "Resolution:", options, 0, False
        )

        file_name = None

        if ok:
            file_name, _ = QFileDialog.getSaveFileName(
                self, "Save File", "", "Images (*.png)"
            )
            if file_name:
                self.saveRender(file_name, resolution)

    def saveRenderDialog(self):
        options = ["1x", "2x", "4x"]
        resolution, ok = QInputDialog.getItem(
            self, "Select Resolution", "Resolution:", options, 0, False
        )

        if ok:
            file_name, _ = QFileDialog.getSaveFileName(
                self, "Save File", "", "Images (*.png)"
            )
            if file_name:
                self.saveRender(file_name, resolution)

                # Confirmation dialog
                msgBox = QMessageBox(self)
                msgBox.setWindowTitle("Render Saved")
                msgBox.setText(f"Image saved to:\n{file_name}")
                msgBox.setStandardButtons(QMessageBox.Open | QMessageBox.Cancel)
                msgBox.setDefaultButton(QMessageBox.Open)

                open_folder_button = msgBox.addButton(
                    "Open Folder", QMessageBox.ActionRole
                )

                result = msgBox.exec_()

                if result == QMessageBox.Open:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(file_name))
                elif msgBox.clickedButton() == open_folder_button:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(Path(file_name).parent))

    def renderToImage(self, scale):
        self.makeCurrent()
        w = self.width() * scale
        h = self.height() * scale
        gl = self.context().functions()
        gl.glViewport(0, 0, w, h)
        fbo = QOpenGLFramebufferObject(
            w, h, QOpenGLFramebufferObject.CombinedDepthStencil
        )

        fbo.bind()
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self.draw(gl)
        fbo.release()
        result = fbo.toImage()
        self.doneCurrent()
        return result

    def saveRender(self, file_name, resolution):
        image = self.renderToImage(float(resolution[0]))
        image.save(file_name)

    def setBackgroundColor(self, color):
        self.backgroundColor = QColor(color)
        self.makeCurrent()
        gl = self.context().functions()
        gl.glClearColor(color.redF(), color.greenF(), color.blueF(), 1)
        self.doneCurrent()

    def updateSettings(self, **kwargs):
        if not kwargs:
            return

        def present_and_changed(key, prev_val):
            return (key in kwargs) and (prev_val != kwargs[key])

        needs_reinit = False
        if present_and_changed("Color Map", self.colormap):
            self.colormap = kwargs["Color Map"]
            needs_reinit = True

        if present_and_changed("Style", self.style):
            self.style = kwargs["Style"]
            needs_reinit = True

        if present_and_changed("Show Mesh Edges", self.show_mesh_edges):
            self.show_mesh_edges = kwargs["Show Mesh Edges"]
            needs_reinit = True

        if present_and_changed("Background Color", self.backgroundColor):
            color = kwargs["Background Color"]
            self.setBackgroundColor(color)

        if present_and_changed("Color By", self.color_by):
            self.color_by = kwargs.get("Color By", self.color_by)
            needs_reinit = True

        if present_and_changed("Point Size", self.point_size):
            self.point_size = float(kwargs["Point Size"])

        if present_and_changed("Projection", self.camera.projectionMode()):
            self.camera.setProjectionMode(kwargs["Projection"])

        if needs_reinit:
            self.initGeometry()

        self.update()

    def resizeGL(self, width, height):
        super().resizeGL(width, height)
        self.aspect_ratio = width / float(height)
        self.screen_size = width, height

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.overlay.setGeometry(self.geometry())

    def wheelEvent(self, event):
        degrees = event.angleDelta() / 8

        steps = degrees.y() / 15

        self.camera.zoom(steps)
        self.update()

    def mousePressEvent(self, event):
        self.lastMousePosition = event.pos()
        if event.button() == QtCore.Qt.RightButton:
            self.rightMouseButtonPressed = True

    def keyPressEvent(self, event):
        dx, dy = 0, 0
        self.restrict_axis = None
        modifiers = event.modifiers()

        if event.key() == Qt.Key_W:
            dy -= 10
        if event.key() == Qt.Key_S:
            dy += 10

        if event.key() == Qt.Key_A:
            dx -= 10
        if event.key() == Qt.Key_D:
            dx += 10

        if event.key() == Qt.Key_C:
            self.camera.storeOrientation()

        if event.key() == Qt.Key_R:
            self.camera.resetOrientation()

        # Check for Shift + X, Y, or Z
        if modifiers & Qt.ShiftModifier:
            if event.key() == Qt.Key_X:
                self.restrict_axis = "shift_x"
            elif event.key() == Qt.Key_Y:
                self.restrict_axis = "shift_y"
            elif event.key() == Qt.Key_Z:
                self.restrict_axis = "shift_z"
        else:
            # Regular X, Y, Z without Shift
            if event.key() == Qt.Key_X:
                self.restrict_axis = "x"
            elif event.key() == Qt.Key_Y:
                self.restrict_axis = "y"
            elif event.key() == Qt.Key_Z:
                self.restrict_axis = "z"
        super().keyPressEvent(event)

        self.camera.orbit(
            dx,
            dy,
        )
        self.update()

    def keyReleaseEvent(self, event):
        if event.key() in (Qt.Key_X, Qt.Key_Y, Qt.Key_Z):
            self.restrict_axis = None
        super().keyReleaseEvent(event)

    def mouseMoveEvent(self, event):
        dx = event.pos().x() - self.lastMousePosition.x()
        dy = event.pos().y() - self.lastMousePosition.y()

        if event.buttons() & QtCore.Qt.LeftButton:
            if self.restrict_axis in {"x", "y", "z"}:
                self.rotatePointCloud(dx, self.restrict_axis)
            else:
                self.camera.orbit(
                    dx,
                    dy,
                    restrict_axis=self.restrict_axis,
                    event_pos=event.pos() - self.geometry().center(),
                )

        elif self.rightMouseButtonPressed:
            self.camera.pan(-dx, dy)  # Negate dx to get correct direction

        self.lastMousePosition = event.pos()
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.RightButton:
            self.rightMouseButtonPressed = False

    def rotatePointCloud(self, dx, axis):

        if self.xyz is None:
            return

        angle = np.radians(dx * self.camera.rotationSpeed)
        cos_a, sin_a = np.cos(angle), np.sin(angle)

        # Define rotation matrices for X, Y, and Z axes
        if axis == "x":
            rotation_matrix = np.array(
                [[1, 0, 0], [0, cos_a, -sin_a], [0, sin_a, cos_a]], dtype=np.float32
            )
        elif axis == "y":
            rotation_matrix = np.array(
                [[cos_a, 0, sin_a], [0, 1, 0], [-sin_a, 0, cos_a]], dtype=np.float32
            )
        elif axis == "z":
            rotation_matrix = np.array(
                [[cos_a, -sin_a, 0], [sin_a, cos_a, 0], [0, 0, 1]], dtype=np.float32
            )
        else:
            return

        # Apply rotation to the XYZ points
        points = self.xyz[:, 3:6]
        rotated_points = points @ rotation_matrix.T

        # Update the point cloud with rotated points
        self.xyz[:, 3:6] = rotated_points
        self.initGeometry()

    def initGeometry(self):
        if self.point_cloud_renderer is None:
            return

        varray = self.updatePointCloudVertices()
        self.point_cloud_renderer.setPoints(varray)
        self.sphere_renderer.setPoints(varray)

        if self.style == "Convex Hull":
            hull = ConvexHull(varray[:, :3])
            mesh = trimesh.Trimesh(vertices=varray[:, :3], faces=hull.simplices)
            # can pass vertex colors here, but I wouldn't
            self.mesh_renderer.setMesh(mesh)

            if self.show_mesh_edges:
                self.line_renderer.setLines(self.mesh_renderer.getLines())

        self.update()

    def updatePointCloudVertices(self):
        self.overlay.setVisible(False)
        logger.debug("Loading Vertices")
        logger.debug(".XYZ shape: %s", self.xyz.shape[0])
        layers = self.xyz[:, 2]
        max_layers = int(np.nanmax(layers[layers < 99]))

        # Loading the point cloud from file
        def vis_pc(xyz, color_axis):
            pcd_points = xyz[:, 3:6]
            pcd_colors = None

            if xyz.shape[1] <= 6 and color_axis >= 6:
                logger.warning(
                    "Old CrystalGrower version! %s option not available for colouring.",
                    self.color_by,
                )
                color_axis = 3

            if color_axis == 3:
                axis_vis = np.arange(0, xyz.shape[0], dtype=np.float32)
            else:
                axis_vis = xyz[:, color_axis]

            if color_axis == 2:
                min_val = 1
                max_val = max_layers
            elif color_axis == -1:
                min_val = 0
                max_val = 0
                axis_vis = np.zeros_like(axis_vis)
            else:
                min_val = np.nanmin(axis_vis)
                max_val = np.nanmax(axis_vis)

            # Avoid division by zero in case all values are the same
            range_val = max_val - min_val if max_val != min_val else 1

            normalized_axis_vis = (axis_vis - min_val) / range_val

            pcd_colors = self.availableColormaps[self.colormap](normalized_axis_vis)[
                :, 0:3
            ]

            return (pcd_points, pcd_colors)

        points, colors = vis_pc(self.xyz, self.columnLabelToIndex[self.color_by])

        if not self.viewInitialized:
            self.camera.fitToObject(points)
            self.viewInitialized = True

        points = np.asarray(points).astype("float32")
        colors = np.asarray(colors).astype("float32")

        try:
            attributes = np.concatenate((points, colors), axis=1)

            return attributes
        except ValueError as exc:
            logger.error(
                "%s\n XYZ %s POINTS %s COLORS %s TYPE %s",
                exc,
                self.xyz.shape,
                points.shape,
                colors.shape,
                self.color_by,
            )
            return

    def initializeGL(self):
        logger.debug(
            "Initialized OpenGL, version info: %s", self.context().format().version()
        )
        debug = False
        if debug:
            self.logger = QOpenGLDebugLogger(self.context())
            if self.logger.initialize():
                self.logger.messageLogged.connect(self.handleLoggedMessage)
            else:
                ext = self.context().hasExtension(QtCore.QByteArray("GL_KHR_debug"))
                logger.debug(
                    "Debug logger not initialized, have extension GL_KHR_debug: %s", ext
                )

        color = self.backgroundColor
        gl = self.context().extraFunctions()
        self.point_cloud_renderer = SimplePointRenderer()
        self.sphere_renderer = SphereRenderer(gl)
        self.mesh_renderer = MeshRenderer(gl)
        self.line_renderer = LineRenderer(gl)
        self.axes_renderer = AxesRenderer()
        gl.glEnable(GL_DEPTH_TEST)
        gl.glClearColor(color.redF(), color.greenF(), color.blueF(), 1)

    def handleLoggedMessage(self, message):
        logger.debug(
            "Source: %s, Type: %s, Message: %s",
            message.source(),
            message.type(),
            message.message(),
        )

    def _draw_points(self, gl, uniforms):
        if self.point_cloud_renderer.numberOfPoints() <= 0:
            return
        self.point_cloud_renderer.bind()
        self.point_cloud_renderer.setUniforms(**uniforms)

        self.point_cloud_renderer.draw(gl)
        self.point_cloud_renderer.release()

    def _draw_spheres(self, gl, uniforms):
        if self.sphere_renderer.numberOfInstances() <= 0:
            return
        self.sphere_renderer.bind(gl)
        self.sphere_renderer.setUniforms(**uniforms)

        self.sphere_renderer.draw(gl)
        self.sphere_renderer.release()

    def _draw_mesh(self, gl, uniforms):
        if self.mesh_renderer.numberOfVertices() <= 0:
            return
        self.mesh_renderer.bind(gl)
        self.mesh_renderer.setUniforms(**uniforms)

        self.mesh_renderer.draw(gl)
        self.mesh_renderer.release()

    def _draw_lines(self, gl, uniforms):
        if self.line_renderer.numberOfVertices() <= 0:
            return
        self.line_renderer.bind(gl)
        self.line_renderer.setUniforms(**uniforms)

        self.line_renderer.draw(gl)
        self.line_renderer.release()

    def draw(self, gl):
        from PySide6.QtGui import QMatrix4x4, QVector2D

        mvp = self.camera.modelViewProjectionMatrix(self.aspect_ratio)
        view = self.camera.viewMatrix()
        proj = self.camera.projectionMatrix(self.aspect_ratio)
        modelView = self.camera.modelViewMatrix()
        axes = QMatrix4x4()
        screen_size = QVector2D(*self.screen_size)

        uniforms = {
            "u_viewMat": view,
            "u_modelViewProjectionMat": mvp,
            "u_pointSize": self.point_size,
            "u_axesMat": axes,
            "u_screenSize": screen_size,
            "u_projectionMat": proj,
            "u_modelViewMat": modelView,
            "u_scale": self.camera.scale,
            "u_lineScale": 2.0,
        }

        if self.style == "Points":
            self._draw_points(gl, uniforms)
        elif self.style == "Spheres":
            self._draw_spheres(gl, uniforms)
        elif self.style == "Convex Hull":
            self._draw_mesh(gl, uniforms)
            if self.show_mesh_edges:
                self._draw_lines(gl, uniforms)

        self.axes_renderer.bind()
        self.axes_renderer.setUniforms(**uniforms)
        self.axes_renderer.draw(gl)
        self.axes_renderer.release()

    def paintGL(self):
        gl = self.context().extraFunctions()
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self.draw(gl)
