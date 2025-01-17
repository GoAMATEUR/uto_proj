""" Helper methods for loading and parsing KITTI data.

Author: Charles R. Qi, Kui Xu
Date: September 2017/2018
"""

import torch
import numpy as np
import cv2
cv2.setNumThreads(0)
import os, math
import matplotlib.pyplot as plt
from lxml import etree
from scipy.optimize import leastsq
from PIL import Image
import pdb
import xlwt
from shapely.geometry import Polygon

TOP_Y_MIN = -30
TOP_Y_MAX = +30
TOP_X_MIN = 0
TOP_X_MAX = 100
TOP_Z_MIN = -3.5
TOP_Z_MAX = 0.6

TOP_X_DIVISION = 0.05
TOP_Y_DIVISION = 0.05
TOP_Z_DIVISION = 0.02

cbox = np.array([[0, 70.4], [-40, 40], [-3, 2]])

def convertRot2Alpha(ry3d, z3d, x3d):
    alpha = ry3d - math.atan2(x3d, z3d)

    # equivalent
    equ_alpha = ry3d - math.atan2(x3d, z3d)
    
    while alpha > math.pi: alpha -= math.pi * 2
    while alpha < (-math.pi): alpha += math.pi * 2

    return alpha

def convertAlpha2Rot(alpha, z3d, x3d):

    ry3d = alpha + math.atan2(x3d, z3d) + 0.5 * math.pi

    while ry3d > math.pi: ry3d -= math.pi * 2
    while ry3d < (-math.pi): ry3d += math.pi * 2

    return ry3d

def refresh_attributes(calib):
    # refresh all attributes when P2 varies
    
    calib.c_u = calib.P[0, 2]
    calib.c_v = calib.P[1, 2]
    calib.f_u = calib.P[0, 0]
    calib.f_v = calib.P[1, 1]
    calib.b_x = calib.P[0, 3] / (-calib.f_u)  # relative
    calib.b_y = calib.P[1, 3] / (-calib.f_v)

class Object3d(object):
    """ 3d object label """

    def __init__(self, label_file_line):
        data = label_file_line.split(" ")
        data[1:] = [float(x) for x in data[1:]]
        # print(data)
        # extract label, truncation, occlusion
        self.type = data[0]  # 'Car', 'Pedestrian', ...
        self.truncation = data[1]  # truncated pixel ratio [0..1]
        self.occlusion = int(
            data[2]
        )  # 0=visible, 1=partly occluded, 2=fully occluded, 3=unknown

        # extract 2d bounding box in 0-based coordinates
        self.xmin = data[4]  # left
        self.ymin = data[5]  # top
        self.xmax = data[6]  # right
        self.ymax = data[7]  # bottom
        self.box2d = np.array([self.xmin, self.ymin, self.xmax, self.ymax], dtype=np.float32)

        # extract 3d bounding box information
        self.h = data[8]  # box height
        self.w = data[9]  # box width
        self.l = data[10]  # box length (in meters)
        self.s = np.array((float(data[9]), float(data[8]), float(data[10])), dtype=np.float32) #shape (w, h, l)
        self.t = np.array((float(data[11]), float(data[12]), float(data[13])), dtype=np.float32)  # location (x,y,z) in camera coord.

        self.dis_to_cam = np.linalg.norm(self.t)

        self.ry = data[14]  # yaw angle (around Y-axis in camera coordinates) [-pi..pi]
        self.real_alpha = data[3]
        self.ray = math.atan2(self.t[0], self.t[2])
        self.alpha = convertRot2Alpha(self.ry, self.t[2], self.t[0])

        # difficulty level 
        self.level_str = None
        self.level = self.get_kitti_obj_level()

        #matched ID
        self.id=0


    def get_kitti_obj_level(self):
        height = float(self.box2d[3]) - float(self.box2d[1]) + 1

        if height >= 40 and self.truncation <= 0.15 and self.occlusion <= 0:
            self.level_str = 'Easy'
            return 0  # Easy
        elif height >= 25 and self.truncation <= 0.3 and self.occlusion <= 1:
            self.level_str = 'Moderate'
            return 1  # Moderate
        elif height >= 25 and self.truncation <= 0.5 and self.occlusion <= 2:
            self.level_str = 'Hard'
            return 2  # Hard
        else:
            self.level_str = 'UnKnown'
            return -1

    def generate_corners3d(self):
        """
        generate corners3d representation for this object
        :return corners_3d: (8, 3) corners of box3d in camera coord
        """
        l, h, w = self.l, self.h, self.w
        x_corners = [l / 2, l / 2, -l / 2, -l / 2, l / 2, l / 2, -l / 2, -l / 2]
        y_corners = [0, 0, 0, 0, -h, -h, -h, -h]
        z_corners = [w / 2, -w / 2, -w / 2, w / 2, w / 2, -w / 2, -w / 2, w / 2]

        R = np.array([[np.cos(self.ry), 0, np.sin(self.ry)],
                      [0, 1, 0],
                      [-np.sin(self.ry), 0, np.cos(self.ry)]])

        corners3d = np.vstack([x_corners, y_corners, z_corners])  # (3, 8)
        corners3d = np.dot(R, corners3d).T
        corners3d = corners3d + self.t

        return corners3d

    def print_object(self):
        print(
            "Type, truncation, occlusion, alpha: %s, %d, %d, %f"
            % (self.type, self.truncation, self.occlusion, self.alpha)
        )
        print(
            "2d bbox (x0,y0,x1,y1): %f, %f, %f, %f"
            % (self.xmin, self.ymin, self.xmax, self.ymax)
        )
        print("3d bbox h,w,l: %f, %f, %f" % (self.h, self.w, self.l))
        print(
            "3d bbox location, ry: (%f, %f, %f), %f"
            % (self.t[0], self.t[1], self.t[2], self.ry)
        )
        print("Difficulty of estimation: {}".format(self.estimate_diffculty()))

def refresh_attributes(calib):
    # refresh all attributes when projection matrix varies
    calib.c_u = calib.P[0, 2]
    calib.c_v = calib.P[1, 2]
    calib.f_u = calib.P[0, 0]
    calib.f_v = calib.P[1, 1]
    calib.b_x = calib.P[0, 3] / (-calib.f_u)  # relative
    calib.b_y = calib.P[1, 3] / (-calib.f_v)
    
def camera_parameter(intrinsic_matrix, rotation_matrix, translation_vector):
    camera_intrinsic = {
        # R，旋转矩阵
        "R": rotation_matrix,
        # t，平移向量
        "T": translation_vector,
        # 焦距，f/dx, f/dy
        "f": [intrinsic_matrix[0], intrinsic_matrix[4]],
        # principal point，主点，主轴与像平面的交点
        "c": [intrinsic_matrix[2], intrinsic_matrix[5]],

        "calib": np.array([[intrinsic_matrix[0], 0, intrinsic_matrix[2], 0],
                           [0, intrinsic_matrix[4], intrinsic_matrix[5], 0],
                           [0, 0, 1., 0]], dtype=np.float32)

    }
    return camera_intrinsic
    
def _get_check_element(root, name, length, default='default'):
    """
     Acquire xml element, and check whether it is valid?
     :param root: root element
     :param name: element name
     :param length: nums of child element, if 0, return all child elements without check
     :return: element value, xml element
     """
    elements = root.findall(name)
    # print(elements)

    if 0 == len(elements):
        # print('a')
        # print(etree.Element(str(default)))
        return etree.Element(str(default))  # 这里的默认值写不上来，目前全都是None，先这样吧
        # return str(default)

    if len(elements) != length:
        raise RuntimeError('The nums of %s is supposed to be %d, '
                           'but is %d' % (name, length, len(elements)))
    if 1 == length:
        elements = elements[0]

    return elements

class Calibration(object):
    """ Calibration matrices and utils
        3d XYZ in <label>.txt are in rect camera coord.
        2d box xy are in image2 coord
        Points in <lidar>.bin are in Velodyne coord.

        y_image2 = P^2_rect * x_rect
        y_image2 = P^2_rect * R0_rect * Tr_velo_to_cam * x_velo
        x_ref = Tr_velo_to_cam * x_velo
        x_rect = R0_rect * x_ref

        P^2_rect = [f^2_u,  0,      c^2_u,  -f^2_u b^2_x;
                    0,      f^2_v,  c^2_v,  -f^2_v b^2_y;
                    0,      0,      1,      0]
                 = K * [1|t]

        image2 coord:
         ----> x-axis (u)
        |
        |
        v y-axis (v)

        velodyne coord:
        front x, left y, up z

        rect/ref camera coord:
        right x, down y, front z

        Ref (KITTI paper): http://www.cvlibs.net/publications/Geiger2013IJRR.pdf

        TODO(rqi): do matrix multiplication only once for each projection.
    """

    def __init__(self, calib_filepath, from_video=False, use_right_cam=False):
        if from_video:
            calibs = self.read_calib_from_video(calib_filepath)
        elif calib_filepath.split('.')[-1]=='xml':
            calibs = self.read_xml_file(calib_filepath)
        else:
            calibs = self.read_calib_file(calib_filepath)
        
        # Projection matrix from rect camera coord to image coord
        self.P = calibs["P3"] if use_right_cam else calibs["P2"]
        self.P = np.reshape(self.P, [3, 4])
        
        # Rigid transform from Velodyne coord to reference camera coord
        self.V2C = calibs["Tr_velo_to_cam"]
        if self.V2C.size == 16:
            self.V2C = self.V2C[:12]
        self.V2C = np.reshape(self.V2C, [3, 4])
        self.C2V = inverse_rigid_trans(self.V2C)
        
        # Rotation from reference camera coord to rect camera coord
        self.R0 = calibs["R0_rect"]
        if self.R0.sum()==0:
            self.R0 = np.eye(3)
        self.R0 = np.reshape(self.R0, [3, 3])

        # Camera intrinsics and extrinsics
        self.c_u = self.P[0, 2]
        self.c_v = self.P[1, 2]
        self.f_u = self.P[0, 0]
        self.f_v = self.P[1, 1]
        self.b_x = self.P[0, 3] / (-self.f_u)  # relative
        self.b_y = self.P[1, 3] / (-self.f_v)

    def read_calib_file(self, filepath):
        """ Read in a calibration file and parse into a dictionary.
        Ref: https://github.com/utiasSTARS/pykitti/blob/master/pykitti/utils.py
        """
        data = {}
        with open(filepath, "r") as f:
            for line in f.readlines():
                line = line.rstrip()
                if len(line) == 0:
                    continue
                key, value = line.split(":", 1)
                # The only non-float values in these files are dates, which
                # we don't care about anyway
                try:
                    data[key] = np.array([float(x) for x in value.split()])
                except ValueError:
                    pass

        return data
    
    def read_xml_file(self, filepath):
        """ Read in a calibration file and parse into a dictionary.
        Ref: https://github.com/utiasSTARS/pykitti/blob/master/pykitti/utils.py
        """
        data = {}
        printpic = False
        annotation = {'image': None, 'camera_param': None, 'annotation': []}
        try:
            xml_tree = etree.parse(filepath)
        except:
            return None
        xml_root = xml_tree.getroot()

        # find <CaptureInfo>
        captureinfo = _get_check_element(xml_root, 'CaptureInfo', 1)
        lidar2camparam = _get_check_element(captureinfo, 'Lidar2CamParam', 1)
        intrinsic_matrix = [1] * 9
        translation_vector = [1] * 3
        info = _get_check_element(lidar2camparam, 'intrinsic_matrix0', 1).text
        if info is not None:
            intrinsic_matrix[0] = float(info)
        else:
            return None
        info = _get_check_element(lidar2camparam, 'intrinsic_matrix1', 1).text
        if info is not None:
            intrinsic_matrix[1] = float(info)
        else:
            return None
        info = _get_check_element(lidar2camparam, 'intrinsic_matrix2', 1).text
        if info is not None:
            intrinsic_matrix[2] = float(info)
        else:
            return None
        info = _get_check_element(lidar2camparam, 'intrinsic_matrix3', 1).text
        if info is not None:
            intrinsic_matrix[3] = float(info)
        else:
            return None
        info = _get_check_element(lidar2camparam, 'intrinsic_matrix4', 1).text
        if info is not None:
            intrinsic_matrix[4] = float(info)
        else:
            return None
        info = _get_check_element(lidar2camparam, 'intrinsic_matrix5', 1).text
        if info is not None:
            intrinsic_matrix[5] = float(info)
        else:
            return None
        info = _get_check_element(lidar2camparam, 'intrinsic_matrix6', 1).text
        if info is not None:
            intrinsic_matrix[6] = float(info)
        else:
            return None
        info = _get_check_element(lidar2camparam, 'intrinsic_matrix7', 1).text
        if info is not None:
            intrinsic_matrix[7] = float(info)
        else:
            return None
        info = _get_check_element(lidar2camparam, 'intrinsic_matrix8', 1).text
        if info is not None:
            intrinsic_matrix[8] = float(info)
        else:
            return None
        info = _get_check_element(lidar2camparam, 'rotation_vector0', 1).text
        if info is not None:
            rotation_vector0 = float(info)
        else:
            return None
        info = _get_check_element(lidar2camparam, 'rotation_vector1', 1).text
        if info is not None:
            rotation_vector1 = float(info)
        else:
            return None
        info = _get_check_element(lidar2camparam, 'rotation_vector2', 1).text
        if info is not None:
            rotation_vector2 = float(info)
        else:
            return None
        p = (float(rotation_vector0), float(rotation_vector1), float(rotation_vector2))
        rotation_matrix = cv2.Rodrigues(p)[0]

        info = _get_check_element(lidar2camparam, 'translation_vector0', 1).text
        if info is not None:
            translation_vector[0] = float(info)
        else:
            return None
        info = _get_check_element(lidar2camparam, 'translation_vector1', 1).text
        if info is not None:
            translation_vector[1] = float(info)
        else:
            return None
        info = _get_check_element(lidar2camparam, 'translation_vector2', 1).text
        if info is not None:
            translation_vector[2] = float(info)
        else:
            return None
        camera_intrinsic = camera_parameter(
            intrinsic_matrix, rotation_matrix, translation_vector)

        Tr_velo_to_cam = np.zeros((3, 4))
        Tr_velo_to_cam[0:3, 0:3] = np.reshape(camera_intrinsic["R"], [3, 3])
        Tr_velo_to_cam[:, 3] = camera_intrinsic["T"]
        data["Tr_velo_to_cam"] = Tr_velo_to_cam
        data["R0_rect"] = np.zeros((3, 4))
        data["P2"] = camera_intrinsic["calib"]

        return data

    def read_calib_from_video(self, calib_root_dir):
        """ Read calibration for camera 2 from video calib files.
            there are calib_cam_to_cam and calib_velo_to_cam under the calib_root_dir
        """
        data = {}
        cam2cam = self.read_calib_file(
            os.path.join(calib_root_dir, "calib_cam_to_cam.txt")
        )
        velo2cam = self.read_calib_file(
            os.path.join(calib_root_dir, "calib_velo_to_cam.txt")
        )
        Tr_velo_to_cam = np.zeros((3, 4))
        Tr_velo_to_cam[0:3, 0:3] = np.reshape(velo2cam["R"], [3, 3])
        Tr_velo_to_cam[:, 3] = velo2cam["T"]
        data["Tr_velo_to_cam"] = np.reshape(Tr_velo_to_cam, [12])
        data["R0_rect"] = cam2cam["R_rect_00"]
        data["P2"] = cam2cam["P_rect_02"]
        return data

    # change cx cy
    def changeNewCenter(self, center_list, transMat):
        center_list.append(1.0)
        newCoord = np.matmul(transMat, np.array(center_list))
        return newCoord
    # change fx fy
    def changeFxFy(self, transMat):
        self.P[0, 0] = self.P[0, 0] * transMat[0, 0]
        self.P[1, 1] = self.P[1, 1] * transMat[1, 1]

    def matAndUpdate(self, transMat):
        
        center_list = [self.c_u, self.c_v]
        newCenter = self.changeNewCenter(center_list, transMat)
        
        self.P[0, 2] = newCenter[0]
        self.P[1, 2] = newCenter[1]
        # change fx fy
        self.changeFxFy(transMat)

        #print(self.P)
        # Camera intrinsics and extrinsics
        self.c_u = self.P[0, 2]
        self.c_v = self.P[1, 2]
        self.f_u = self.P[0, 0]
        self.f_v = self.P[1, 1]
        self.b_x = self.P[0, 3] / (-self.f_u)  # relative
        self.b_y = self.P[1, 3] / (-self.f_v)

    def cart2hom(self, pts_3d):
        """ Input: nx3 points in Cartesian
            Oupput: nx4 points in Homogeneous by pending 1
        """
        n = pts_3d.shape[0]
        pts_3d_hom = np.hstack((pts_3d, np.ones((n, 1))))
        return pts_3d_hom

    # ===========================
    # ------- 3d to 3d ----------
    # ===========================
    def project_velo_to_ref(self, pts_3d_velo):
        pts_3d_velo = self.cart2hom(pts_3d_velo)  # nx4
        return np.dot(pts_3d_velo, np.transpose(self.V2C))

    def project_ref_to_velo(self, pts_3d_ref):
        pts_3d_ref = self.cart2hom(pts_3d_ref)  # nx4
        return np.dot(pts_3d_ref, np.transpose(self.C2V))

    def project_rect_to_ref(self, pts_3d_rect):
        """ Input and Output are nx3 points """
        return np.transpose(np.dot(np.linalg.inv(self.R0), np.transpose(pts_3d_rect)))

    def project_ref_to_rect(self, pts_3d_ref):
        """ Input and Output are nx3 points """
        return np.transpose(np.dot(self.R0, np.transpose(pts_3d_ref)))

    def project_rect_to_velo(self, pts_3d_rect):
        """ Input: nx3 points in rect camera coord.
            Output: nx3 points in velodyne coord.
        """
        pts_3d_ref = self.project_rect_to_ref(pts_3d_rect)
        return self.project_ref_to_velo(pts_3d_ref)

    def project_velo_to_rect(self, pts_3d_velo):
        pts_3d_ref = self.project_velo_to_ref(pts_3d_velo)
        return self.project_ref_to_rect(pts_3d_ref)

    # ===========================
    # ------- 3d to 2d ----------
    # ===========================
    def project_rect_to_image_tensor(self, pts_3d_rect):
        """ Input: nx3 points in rect camera coord.
            Output: nx2 points in image2 coord.
        """
        pts_3d_rect = torch.cat((pts_3d_rect, torch.ones(pts_3d_rect.shape[0], 1).type_as(pts_3d_rect)), dim=1)
        pts_2d = torch.matmul(pts_3d_rect, torch.from_numpy(self.P).type_as(pts_3d_rect).t())  # nx3
        pts_2d_ = pts_2d.clone()
        pts_2d_[:, 0] /= pts_2d[:, 2]
        pts_2d_[:, 1] /= pts_2d[:, 2]

        return pts_2d_[:, 0:2], pts_2d_[:, 2]

    def project_rect_to_image(self, pts_3d_rect):
        """ Input: nx3 points in rect camera coord.
            Output: nx2 points in image2 coord.
        """
        pts_3d_rect = self.cart2hom(pts_3d_rect)
        pts_2d = np.dot(pts_3d_rect, np.transpose(self.P))  # nx3
        pts_2d[:, 0] /= pts_2d[:, 2]
        pts_2d[:, 1] /= pts_2d[:, 2]
        return pts_2d[:, 0:2], pts_2d[:, 2]

    def project_velo_to_image(self, pts_3d_velo):
        """ Input: nx3 points in velodyne coord.
            Output: nx2 points in image2 coord.
        """
        pts_3d_rect = self.project_velo_to_rect(pts_3d_velo)
        return self.project_rect_to_image(pts_3d_rect)

    def project_8p_to_4p(self, pts_2d):
        x0 = np.min(pts_2d[:, 0])
        x1 = np.max(pts_2d[:, 0])
        y0 = np.min(pts_2d[:, 1])
        y1 = np.max(pts_2d[:, 1])
        x0 = max(0, x0)
        # x1 = min(x1, proj.image_width)
        y0 = max(0, y0)
        # y1 = min(y1, proj.image_height)
        return np.array([x0, y0, x1, y1])

    def project_velo_to_4p(self, pts_3d_velo):
        """ Input: nx3 points in velodyne coord.
            Output: 4 points in image2 coord.
        """
        pts_2d_velo = self.project_velo_to_image(pts_3d_velo)
        return self.project_8p_to_4p(pts_2d_velo)

    # ===========================
    # ------- 2d to 3d ----------
    # ===========================
    def project_image_to_rect(self, uv_depth):
        """ Input: nx3 first two channels are uv, 3rd channel
                   is depth in rect camera coord.
            Output: nx3 points in rect camera coord.
        """
        n = uv_depth.shape[0]
        x = ((uv_depth[:, 0] - self.c_u) * uv_depth[:, 2]) / self.f_u + self.b_x
        y = ((uv_depth[:, 1] - self.c_v) * uv_depth[:, 2]) / self.f_v + self.b_y
        
        if isinstance(uv_depth, np.ndarray):
            pts_3d_rect = np.zeros((n, 3))
        else:
            # torch.Tensor or torch.cuda.Tensor
            pts_3d_rect = uv_depth.new(uv_depth.shape).zero_()

        pts_3d_rect[:, 0] = x
        pts_3d_rect[:, 1] = y
        pts_3d_rect[:, 2] = uv_depth[:, 2]

        return pts_3d_rect

    def project_image_to_velo(self, uv_depth):
        pts_3d_rect = self.project_image_to_rect(uv_depth)
        return self.project_rect_to_velo(pts_3d_rect)

    def project_depth_to_velo(self, depth, constraint_box=True):
        depth_pt3d = get_depth_pt3d(depth)
        depth_UVDepth = np.zeros_like(depth_pt3d)
        depth_UVDepth[:, 0] = depth_pt3d[:, 1]
        depth_UVDepth[:, 1] = depth_pt3d[:, 0]
        depth_UVDepth[:, 2] = depth_pt3d[:, 2]
        # print("depth_pt3d:",depth_UVDepth.shape)
        depth_pc_velo = self.project_image_to_velo(depth_UVDepth)
        # print("dep_pc_velo:",depth_pc_velo.shape)
        if constraint_box:
            depth_box_fov_inds = (
                (depth_pc_velo[:, 0] < cbox[0][1])
                & (depth_pc_velo[:, 0] >= cbox[0][0])
                & (depth_pc_velo[:, 1] < cbox[1][1])
                & (depth_pc_velo[:, 1] >= cbox[1][0])
                & (depth_pc_velo[:, 2] < cbox[2][1])
                & (depth_pc_velo[:, 2] >= cbox[2][0])
            )
            depth_pc_velo = depth_pc_velo[depth_box_fov_inds]
        return depth_pc_velo


def get_depth_pt3d(depth):
    pt3d = []
    for i in range(depth.shape[0]):
        for j in range(depth.shape[1]):
            pt3d.append([i, j, depth[i, j]])
    return np.array(pt3d)


def rotx(t):
    """ 3D Rotation about the x-axis. """
    c = np.cos(t)
    s = np.sin(t)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def roty(t):
    """ Rotation about the y-axis. """
    c = np.cos(t)
    s = np.sin(t)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rotz(t):
    """ Rotation about the z-axis. """
    c = np.cos(t)
    s = np.sin(t)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def transform_from_rot_trans(R, t):
    """ Transforation matrix from rotation matrix and translation vector. """
    R = R.reshape(3, 3)
    t = t.reshape(3, 1)
    return np.vstack((np.hstack([R, t]), [0, 0, 0, 1]))


def inverse_rigid_trans(Tr):
    """ Inverse a rigid body transform matrix (3x4 as [R|t])
        [R'|-R't; 0|1]
    """
    inv_Tr = np.zeros_like(Tr)  # 3x4
    inv_Tr[0:3, 0:3] = np.transpose(Tr[0:3, 0:3])
    inv_Tr[0:3, 3] = np.dot(-np.transpose(Tr[0:3, 0:3]), Tr[0:3, 3])
    return inv_Tr


def read_label(label_filename):
    lines = [line.rstrip() for line in open(label_filename)]
    objects = [Object3d(line) for line in lines if line !='']
    return objects


def load_image(img_filename):
    return cv2.imread(img_filename)

def load_depth_v(img_filename):
    # return cv2.imread(img_filename)
    disp_img = cv2.imread(img_filename, cv2.IMREAD_UNCHANGED)
    disp_img = disp_img.astype(np.float)
    return disp_img / 256.0


def load_depth0(img_filename):
    # return cv2.imread(img_filename)
    depth_img = np.array(Image.open(img_filename), dtype=int)

    depth_img = depth_img.astype(np.float) / 256.0

    return depth_img


def load_depth(img_filename):
    isexist = True
    disp_img = cv2.imread(img_filename, cv2.IMREAD_UNCHANGED)
    if disp_img is None:
        isexist = False
        disp_img = np.zeros((370, 1224))
    else:
        disp_img = disp_img.astype(np.float)
    return disp_img / 256.0, isexist


def load_velo_scan(velo_filename, dtype=np.float32, n_vec=4):
    scan = np.fromfile(velo_filename, dtype=dtype)
    scan = scan.reshape((-1, n_vec))
    return scan

def lidar_to_top_coords(x, y, z=None):
    if 0:
        return x, y
    else:
        # print("TOP_X_MAX-TOP_X_MIN:",TOP_X_MAX,TOP_X_MIN)
        X0, Xn = 0, int((TOP_X_MAX - TOP_X_MIN) // TOP_X_DIVISION) + 1
        Y0, Yn = 0, int((TOP_Y_MAX - TOP_Y_MIN) // TOP_Y_DIVISION) + 1
        xx = Yn - int((y - TOP_Y_MIN) // TOP_Y_DIVISION)
        yy = Xn - int((x - TOP_X_MIN) // TOP_X_DIVISION)

        return xx, yy

def lidar_to_top(lidar):

    idx = np.where(lidar[:, 0] > TOP_X_MIN)
    lidar = lidar[idx]
    idx = np.where(lidar[:, 0] < TOP_X_MAX)
    lidar = lidar[idx]

    idx = np.where(lidar[:, 1] > TOP_Y_MIN)
    lidar = lidar[idx]
    idx = np.where(lidar[:, 1] < TOP_Y_MAX)
    lidar = lidar[idx]

    idx = np.where(lidar[:, 2] > TOP_Z_MIN)
    lidar = lidar[idx]
    idx = np.where(lidar[:, 2] < TOP_Z_MAX)
    lidar = lidar[idx]

    pxs = lidar[:, 0]
    pys = lidar[:, 1]
    pzs = lidar[:, 2]
    prs = lidar[:, 3]
    qxs = ((pxs - TOP_X_MIN) // TOP_X_DIVISION).astype(np.int32)
    qys = ((pys - TOP_Y_MIN) // TOP_Y_DIVISION).astype(np.int32)
    # qzs=((pzs-TOP_Z_MIN)//TOP_Z_DIVISION).astype(np.int32)
    qzs = (pzs - TOP_Z_MIN) / TOP_Z_DIVISION
    quantized = np.dstack((qxs, qys, qzs, prs)).squeeze()

    X0, Xn = 0, int((TOP_X_MAX - TOP_X_MIN) // TOP_X_DIVISION) + 1
    Y0, Yn = 0, int((TOP_Y_MAX - TOP_Y_MIN) // TOP_Y_DIVISION) + 1
    Z0, Zn = 0, int((TOP_Z_MAX - TOP_Z_MIN) / TOP_Z_DIVISION)
    height = Xn - X0
    width = Yn - Y0
    channel = Zn - Z0 + 2
    # print('height,width,channel=%d,%d,%d'%(height,width,channel))
    top = np.zeros(shape=(height, width, channel), dtype=np.float32)

    # histogram = Bin(channel, 0, Zn, "z", Bin(height, 0, Yn, "y", Bin(width, 0, Xn, "x", Maximize("intensity"))))
    # histogram.fill.numpy({"x": qxs, "y": qys, "z": qzs, "intensity": prs})

    if 1:  # new method
        for x in range(Xn):
            ix = np.where(quantized[:, 0] == x)
            quantized_x = quantized[ix]
            if len(quantized_x) == 0:
                continue
            yy = -x

            for y in range(Yn):
                iy = np.where(quantized_x[:, 1] == y)
                quantized_xy = quantized_x[iy]
                count = len(quantized_xy)
                if count == 0:
                    continue
                xx = -y

                top[yy, xx, Zn + 1] = min(1, np.log(count + 1) / math.log(32))
                max_height_point = np.argmax(quantized_xy[:, 2])
                top[yy, xx, Zn] = quantized_xy[max_height_point, 3]

                for z in range(Zn):
                    iz = np.where(
                        (quantized_xy[:, 2] >= z) & (quantized_xy[:, 2] <= z + 1)
                    )
                    quantized_xyz = quantized_xy[iz]
                    if len(quantized_xyz) == 0:
                        continue
                    zz = z

                    # height per slice
                    max_height = max(0, np.max(quantized_xyz[:, 2]) - z)
                    top[yy, xx, zz] = max_height

    # if 0: #unprocess
    #     top_image = np.zeros((height,width,3),dtype=np.float32)
    #
    #     num = len(lidar)
    #     for n in range(num):
    #         x,y = qxs[n],qys[n]
    #         if x>=0 and x <width and y>0 and y<height:
    #             top_image[y,x,:] += 1
    #
    #     max_value=np.max(np.log(top_image+0.001))
    #     top_image = top_image/max_value *255
    #     top_image=top_image.astype(dtype=np.uint8)
    return top


def proj3d_to_2d(rgbpoint):
    x0 = np.min(rgbpoint[:, 0])
    x1 = np.max(rgbpoint[:, 0])
    y0 = np.min(rgbpoint[:, 1])
    y1 = np.max(rgbpoint[:, 1])

    
    return np.array([x0, y0, x1, y1])


def project_to_image(pts_3d, P):
    """ Project 3d points to image plane.

    Usage: pts_2d = projectToImage(pts_3d, P)
      input: pts_3d: nx3 matrix
             P:      3x4 projection matrix
      output: pts_2d: nx2 matrix

      P(3x4) dot pts_3d_extended(4xn) = projected_pts_2d(3xn)
      => normalize projected_pts_2d(2xn)

      <=> pts_3d_extended(nx4) dot P'(4x3) = projected_pts_2d(nx3)
          => normalize projected_pts_2d(nx2)
    """
    n = pts_3d.shape[0]
    pts_3d_extend = np.hstack((pts_3d, np.ones((n, 1))))
    # print(('pts_3d_extend shape: ', pts_3d_extend.shape))
    pts_2d = np.dot(pts_3d_extend, np.transpose(P))  # nx3
    pts_2d[:, 0] /= pts_2d[:, 2]
    pts_2d[:, 1] /= pts_2d[:, 2]
    return pts_2d[:, 0:2]


def compute_box_3d(obj, P):
    """ Takes an object and a projection matrix (P) and projects the 3d
        bounding box into the image plane.
        Returns:
            corners_2d: (8,2) array in left image coord.
            corners_3d: (8,3) array in in rect camera coord.
    """
    # compute rotational matrix around yaw axis
    R = roty(obj.ry)

    # 3d bounding box dimensions
    l = obj.l
    w = obj.w
    h = obj.h

    # 3d bounding box corners
    x_corners = [l / 2, l / 2, -l / 2, -l / 2, l / 2, l / 2, -l / 2, -l / 2]
    y_corners = [0, 0, 0, 0, -h, -h, -h, -h]
    z_corners = [w / 2, -w / 2, -w / 2, w / 2, w / 2, -w / 2, -w / 2, w / 2]

    # rotate and translate 3d bounding box
    corners_3d = np.dot(R, np.vstack([x_corners, y_corners, z_corners]))
    # print corners_3d.shape
    corners_3d[0, :] = corners_3d[0, :] + obj.t[0]
    corners_3d[1, :] = corners_3d[1, :] + obj.t[1]
    corners_3d[2, :] = corners_3d[2, :] + obj.t[2]
    # print 'cornsers_3d: ', corners_3d
    # only draw 3d bounding box for objs in front of the camera
    if np.any(corners_3d[2, :] < 0.1):
        corners_2d = None
        return corners_2d, np.transpose(corners_3d)

    # project the 3d bounding box into the image plane
    corners_2d = project_to_image(np.transpose(corners_3d), P)
    # print 'corners_2d: ', corners_2d
    return corners_2d, np.transpose(corners_3d)

def compute_orientation_3d(obj, P):
    """ Takes an object and a projection matrix (P) and projects the 3d
        object orientation vector into the image plane.
        Returns:
            orientation_2d: (2,2) array in left image coord.
            orientation_3d: (2,3) array in in rect camera coord.
    """

    # compute rotational matrix around yaw axis
    R = roty(obj.ry)

    # orientation in object coordinate system
    orientation_3d = np.array([[0.0, obj.l], [0, 0], [0, 0]])

    # rotate and translate in camera coordinate system, project in image
    orientation_3d = np.dot(R, orientation_3d)
    orientation_3d[0, :] = orientation_3d[0, :] + obj.t[0]
    orientation_3d[1, :] = orientation_3d[1, :] + obj.t[1]
    orientation_3d[2, :] = orientation_3d[2, :] + obj.t[2]

    # vector behind image plane?
    if np.any(orientation_3d[2, :] < 0.1):
        orientation_2d = None
        return orientation_2d, np.transpose(orientation_3d)

    # project orientation into the image plane
    orientation_2d = project_to_image(np.transpose(orientation_3d), P)
    return orientation_2d, np.transpose(orientation_3d)


def draw_dotted_line(img, pt1, pt2, color, thickness=1, style='dotted', gap=4):
    dist =((pt1[0]-pt2[0])**2+(pt1[1]-pt2[1])**2)**.5
    pts= []
    for i in  np.arange(0,dist,gap):
        r=i/dist
        x=int((pt1[0]*(1-r)+pt2[0]*r)+.5)
        y=int((pt1[1]*(1-r)+pt2[1]*r)+.5)
        p = (x,y)
        pts.append(p)

    if style=='dotted':
        for p in pts:
            cv2.circle(img,p,thickness,color,-1)
    else:
        s=pts[0]
        e=pts[0]
        i=0
        for p in pts:
            s=e
            e=p
            if i%2==1:
                cv2.line(img,s,e,color,thickness)
            i+=1


def draw_projected_box3d(image, qs, id, color=None, cls=None, thickness=2, draw_orientation=False, draw_height_text=False,
                        draw_center_line=False, draw_corner=True, draw_number=False, corner_size=3, draw_corner_line=False, 
                        draw_orien_surface=True, draw_id=False):
    """ Draw 3d bounding box in image
        qs: (8,3) array of vertices for the 3d box in following order:
            1 -------- 0
           /|         /|
          2 -------- 3 .
          | |        | |
          . 5 -------- 4
          |/         |/
          6 -------- 7
    """

    if cls is not None and color is None:
        if cls == 'Car':
            # color = (55, 67, 225)
            color = (0, 255, 255)
        elif cls == 'Pedestrian':
            color = (255, 225, 255)
        elif cls == 'Cyclist':
            color = (139, 0, 0)

    qs = qs.astype(np.int32)
    
    #Draw lines
    for k in range(0, 4):
        # Ref: http://docs.enthought.com/mayavi/mayavi/auto/mlab_helper_functions.html
        i, j = k, (k + 1) % 4
        # use LINE_AA for opencv3
        # cv2.line(image, (qs[i,0],qs[i,1]), (qs[j,0],qs[j,1]), color, thickness, cv2.CV_AA)
        cv2.line(image, (qs[i, 0], qs[i, 1]), (qs[j, 0], qs[j, 1]), color, thickness, cv2.LINE_AA)
        i, j = k + 4, (k + 1) % 4 + 4
        cv2.line(image, (qs[i, 0], qs[i, 1]), (qs[j, 0], qs[j, 1]), color, thickness, cv2.LINE_AA)

        i, j = k, k + 4
        cv2.line(image, (qs[i, 0], qs[i, 1]), (qs[j, 0], qs[j, 1]), color, thickness, cv2.LINE_AA)

    # draw the front: x = l / 2, link 0 <-> 4 and 1 <-> 5
    if draw_orientation:
        cv2.line(image, (qs[0, 0], qs[0, 1]), (qs[5, 0], qs[5, 1]), color, thickness, cv2.LINE_AA)
        cv2.line(image, (qs[1, 0], qs[1, 1]), (qs[4, 0], qs[4, 1]), color, thickness, cv2.LINE_AA)

    if draw_orien_surface:
        orien_mask = np.zeros((image.shape), dtype=np.uint8)
        # orien contours
        contours = qs[[0, 1, 5, 4], :].astype(np.int64)
        orien_mask = cv2.fillPoly(orien_mask, pts=[contours], color=color)
        image = cv2.addWeighted(image, 1, orien_mask, 0.2, 0)
        # contours = qs[[0, 3, 7, 4], :].astype(np.int64)
        # orien_mask = cv2.fillPoly(orien_mask, pts=[contours], color=color)
        # image = cv2.addWeighted(image, 1, orien_mask, 0.2, 0)
        # contours = qs[[4, 5, 6, 7], :].astype(np.int64)
        # orien_mask = cv2.fillPoly(orien_mask, pts=[contours], color=color)
        # image = cv2.addWeighted(image, 1, orien_mask, 0.2, 0)
        # contours = qs[[3, 2, 6, 7], :].astype(np.int64)
        # orien_mask = cv2.fillPoly(orien_mask, pts=[contours], color=color)
        # image = cv2.addWeighted(image, 1, orien_mask, 0.2, 0)
        # contours = qs[[1, 2, 6, 5], :].astype(np.int64)
        # orien_mask = cv2.fillPoly(orien_mask, pts=[contours], color=color)
        # image = cv2.addWeighted(image, 1, orien_mask, 0.2, 0)
        # contours = qs[[0, 1, 2, 3], :].astype(np.int64)
        # orien_mask = cv2.fillPoly(orien_mask, pts=[contours], color=color)
        # image = cv2.addWeighted(image, 1, orien_mask, 0.2, 0)

    # draw colorful vertices, 0=4, 1=5, 2=6, 3=7, 9=10
    colors = [(43, 253, 63), (227, 34, 243), (254, 117, 34), (30, 255, 254),
            (43, 253, 63), (227, 34, 243), (254, 117, 34), (30, 255, 254), (255, 216, 49), (255, 216, 49)]

    # colors = [(0, 0, 205), (255, 20, 147), (255, 255, 0), (0, 255, 255), 
            # (0, 0, 205), (255, 20, 147), (255, 255, 0), (0, 255, 255), (255, 48, 48), (255, 48, 48)]

    if draw_center_line:
        draw_dotted_line(image, (qs[9, 0], qs[9, 1]), (qs[8, 0], qs[8, 1]), colors[-1], 1, cv2.LINE_AA)

    if draw_height_text:
        height_text_size = 0.5
        height_pos = (qs[:4] + qs[4:8]) / 2
        height_pos_center = qs[8:].mean(axis=0)
        for i in range(height_pos.shape[0]):
            cv2.putText(image, 'h_{}'.format(i + 1), tuple(height_pos[i].astype(np.int)), cv2.FONT_HERSHEY_SIMPLEX, 
                                height_text_size, (255, 0, 0), 1, cv2.LINE_AA)

        cv2.putText(image, 'h_c'.format(i + 1), tuple(height_pos_center.astype(np.int)), cv2.FONT_HERSHEY_SIMPLEX, 
                            height_text_size, (255, 0, 0), 1, cv2.LINE_AA)


    if type(draw_corner) == bool:
        if draw_corner:
            for i in range(qs.shape[0]):
                cv2.circle(image, tuple(qs[i]), corner_size, colors[i], -1)

                if draw_corner_line:
                    if i in [0, 1, 2, 3]:
                        cv2.line(image, (qs[i, 0], qs[i, 1]), (qs[i + 4, 0], qs[i + 4, 1]), colors[i], thickness, cv2.LINE_AA)
                    elif i == 8:
                        cv2.line(image, (qs[i, 0], qs[i, 1]), (qs[i + 1, 0], qs[i + 1, 1]), colors[i], thickness, cv2.LINE_AA)

                if draw_number:
                    cv2.putText(image, str(i), tuple(qs[i]), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 0), 1, cv2.LINE_AA)
    
    elif type(draw_corner) == list:
        for i in draw_corner:
            cv2.circle(image, tuple(qs[i]), corner_size, colors[i], -1)

            if draw_corner_line:
                if i in [0, 1, 2, 3]:
                    cv2.line(image, (qs[i, 0], qs[i, 1]), (qs[i + 4, 0], qs[i + 4, 1]), colors[i], thickness, cv2.LINE_AA)
                elif i == 8:
                    cv2.line(image, (qs[i, 0], qs[i, 1]), (qs[i + 1, 0], qs[i + 1, 1]), colors[i], thickness, cv2.LINE_AA)

            if draw_number:
                cv2.putText(image, str(i), tuple(qs[i]), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 0), 1, cv2.LINE_AA)
    
    if draw_id:
        if id !=0:
            cv2.putText(image,str(id),(qs[0, 0], qs[0, 1]),cv2.FONT_HERSHEY_SIMPLEX,3,(255, 0, 0),3)


    return image

def draw_top_image(lidar_top):
    top_image = np.sum(lidar_top, axis=2)
    top_image = top_image - np.min(top_image)
    divisor = np.max(top_image) - np.min(top_image)
    top_image = top_image / divisor * 255
    top_image = np.dstack((top_image, top_image, top_image)).astype(np.uint8)

    return top_image

def init_bev_image(out_size=768):
    bird_view = np.ones((out_size, out_size, 3), dtype=np.uint8) * 230

    return bird_view

def draw_bev_box3d(image, boxes3d, id, color=(255, 255, 255), cls=None, thickness=1, scores=None, text_lables=[], world_size=64, out_size=768, draw_id=True):
    if color is None:   
        if cls is not None:
            if cls == 'Car':
                # color = (55, 67, 225)
                color = (0, 255, 255)
            elif cls == 'Pedestrian':
                color = (255, 225, 255)
            elif cls == 'Cyclist':
                color = (139, 0, 0)
        else:
            color = (0, 0, 255)
    
    for idx in range(boxes3d.shape[0]):
        pts = boxes3d[idx][[0, 1, 2, 3]][:, [0, 2]]
        pts[:, 0] += world_size / 2
        pts[:, 1] = world_size - pts[:, 1]
        pts = (pts * out_size / world_size).astype(np.int32)

        cv2.polylines(image, [pts.reshape(-1, 1, 2)], True, color, 2, lineType=cv2.LINE_AA)
        cv2.line(image,(pts[0][0], pts[0][1]), (pts[1][0],pts[1][1]), color, 4, lineType=cv2.LINE_AA)
        if draw_id:
            if id !=0 and (pts[1][0], pts[1][1])!=(400,800):
                cv2.putText(image,str(id),(pts[1][0], pts[1][1]),cv2.FONT_HERSHEY_SIMPLEX,1,color,1,lineType=cv2.LINE_AA)

    return image

def draw_box3d_on_top(
    image,
    boxes3d,
    color=(255, 255, 255),
    thickness=1,
    scores=None,
    text_lables=[],
    is_gt=False,
):

    # if scores is not None and scores.shape[0] >0:
    # print(scores.shape)
    # scores=scores[:,0]
    font = cv2.FONT_HERSHEY_SIMPLEX
    img = image.copy()
    num = len(boxes3d)
    startx = 5
    for n in range(num):
        b = boxes3d[n]
        x0 = b[0, 0]
        y0 = b[0, 1]
        x1 = b[1, 0]
        y1 = b[1, 1]
        x2 = b[2, 0]
        y2 = b[2, 1]
        x3 = b[3, 0]
        y3 = b[3, 1]
        u0, v0 = lidar_to_top_coords(x0, y0)
        u1, v1 = lidar_to_top_coords(x1, y1)
        u2, v2 = lidar_to_top_coords(x2, y2)
        u3, v3 = lidar_to_top_coords(x3, y3)
        # if is_gt:
        #     color = (0, 255, 0)
        #     startx = 5
        # else:
        #     color = heat_map_rgb(0.0, 1.0, scores[n]) if scores is not None else 255
        #     startx = 85
        cv2.line(img, (u0, v0), (u1, v1), color, thickness, cv2.LINE_AA)
        cv2.line(img, (u1, v1), (u2, v2), color, thickness, cv2.LINE_AA)
        cv2.line(img, (u2, v2), (u3, v3), color, thickness, cv2.LINE_AA)
        cv2.line(img, (u3, v3), (u0, v0), color, thickness, cv2.LINE_AA)
    
    # for n in range(len(text_lables)):
    #     text_pos = (startx, 25 * (n + 1))
    #     cv2.putText(img, text_lables[n], text_pos, font, 0.5, color, 0, cv2.LINE_AA)
        
    return img


# hypothesis function
def hypothesis_func(w, x):
    w1, w0 = w
    return w1 * x + w0

# error function
def error_func(w, train_x, train_y):
    return hypothesis_func(w, train_x) - train_y


def dump_fit_func(w_fit):
    w1, w0 = w_fit
    print("fitting line=", str(w1) + "*x + " + str(w0))
    return


# square error
def dump_fit_cost(w_fit, train_x, train_y):
    error = error_func(w_fit, train_x, train_y)
    square_error = sum(e * e for e in error)
    print("fitting cost:", str(square_error))
    return square_error


def linear_regression(train_x, train_y, test_x):
    # train set
    # train_x = np.array([8.19,2.72,6.39,8.71,4.7,2.66,3.78])
    # train_y = np.array([7.01,2.78,6.47,6.71,4.1,4.23,4.05])

    # linear regression by leastsq
    # msg = "invoke scipy leastsq"
    w_init = [20, 1]  # weight factor init
    fit_ret = leastsq(error_func, w_init, args=(train_x, train_y))
    w_fit = fit_ret[0]

    # dump fit result
    dump_fit_func(w_fit)
    fit_cost = dump_fit_cost(w_fit, train_x, train_y)

    # test set
    # test_x = np.array(np.arange(train_x.min(), train_x.max(), 1.0))
    test_y = hypothesis_func(w_fit, test_x)
    test_y0 = hypothesis_func(w_fit, train_x)
    return test_y, test_y0

def get_iou3d(corners3d, query_corners3d, need_bev=False):
    """	
    :param corners3d: (N, 8, 3) in rect coords	
    :param query_corners3d: (M, 8, 3)	
    :return:	
    """
    from shapely.geometry import Polygon
    A, B = corners3d, query_corners3d
    N, M = A.shape[0], B.shape[0]
    iou3d = np.zeros((N, M), dtype=np.float32)
    iou_bev = np.zeros((N, M), dtype=np.float32)

    # for height overlap, since y face down, use the negative y
    min_h_a = -A[:, 0:4, 1].sum(axis=1) / 4.0
    max_h_a = -A[:, 4:8, 1].sum(axis=1) / 4.0
    min_h_b = -B[:, 0:4, 1].sum(axis=1) / 4.0
    max_h_b = -B[:, 4:8, 1].sum(axis=1) / 4.0

    for i in range(N):
        for j in range(M):
            max_of_min = np.max([min_h_a[i], min_h_b[j]])
            min_of_max = np.min([max_h_a[i], max_h_b[j]])	
            h_overlap = np.max([0, min_of_max - max_of_min])
            if h_overlap == 0:
                continue

            bottom_a, bottom_b = Polygon(A[i, 0:4, [0, 2]].T), Polygon(B[j, 0:4, [0, 2]].T)
            if bottom_a.is_valid and bottom_b.is_valid:
                # check is valid,  A valid Polygon may not possess any overlapping exterior or interior rings.
                bottom_overlap = bottom_a.intersection(bottom_b).area
            else:
                bottom_overlap = 0.
                
            overlap3d = bottom_overlap * h_overlap
            union3d = bottom_a.area * (max_h_a[i] - min_h_a[i]) + bottom_b.area * (max_h_b[j] - min_h_b[j]) - overlap3d
            iou3d[i][j] = overlap3d / union3d
            iou_bev[i][j] = bottom_overlap / (bottom_a.area + bottom_b.area - bottom_overlap)

    if need_bev:
        return iou3d, iou_bev

    return iou3d

def draw_trunc_heatmap(proj_center, box2d, img_size):
    center_2d = (box2d[:2] + box2d[2:]) / 2
    

def approx_proj_center(proj_center, surface_centers, img_size):
    # surface_inside
    img_w, img_h = img_size
    surface_center_inside_img = (surface_centers[:, 0] >= 0) & (surface_centers[:, 1] >= 0) & \
                            (surface_centers[:, 0] <= img_w - 1) & (surface_centers[:, 1] <= img_h - 1)

    if surface_center_inside_img.sum() > 0:
        target_surface_center = surface_centers[surface_center_inside_img.argmax()]
        # y = ax + b
        a, b = np.polyfit([proj_center[0], target_surface_center[0]], [proj_center[1], target_surface_center[1]], 1)
        valid_intersects = []
        valid_edge = []

        left_y = b
        if (0 <= left_y <= img_h - 1):
            valid_intersects.append(np.array([0, left_y]))
            valid_edge.append(0)

        right_y = (img_w - 1) * a + b
        if (0 <= right_y <= img_h - 1):
            valid_intersects.append(np.array([img_w - 1, right_y]))
            valid_edge.append(1)

        top_x = -b / a
        if (0 <= top_x <= img_w - 1):
            valid_intersects.append(np.array([top_x, 0]))
            valid_edge.append(2)

        bottom_x = (img_h - 1 - b) / a
        if (0 <= bottom_x <= img_w - 1):
            valid_intersects.append(np.array([bottom_x, img_h - 1]))
            valid_edge.append(3)

        valid_intersects = np.stack(valid_intersects)
        min_idx = np.argmin(np.linalg.norm(valid_intersects - proj_center.reshape(1, 2), axis=1))
        
        return valid_intersects[min_idx], valid_edge[min_idx]
    else:
        return None

def get_3d_dis(image, objs, calib):
    image = np.array(image)
    obj = objs[0]
    point_radius = 2

    corners_3d = obj.generate_corners3d()
    corners_2d, _ = calib.project_rect_to_image(corners_3d)

    box2d = proj3d_to_2d(corners_2d).astype(np.int) 

    center_3d = obj.t.copy()
    center_3d[1] = center_3d[1] - obj.h / 2
    center_2d, _ = calib.project_rect_to_image(center_3d.reshape(-1, 3))
    center_2d = center_2d.astype(np.int)

    center_idxs = np.array([[0, 1, 4, 5], [0, 1, 2, 3], [0, 3, 4, 7]])
    # shape: 3 x 4 x 3
    centers = corners_3d[center_idxs].mean(axis=1)
    centers_2d = corners_2d[center_idxs].mean(axis=1).astype(np.int)
    # vectors
    vectors = centers_2d - center_2d.reshape(1, 2)
    vectors = vectors / np.sqrt(np.sum(vectors ** 2, axis=1)).reshape(-1, 1)

    image = draw_projected_box3d(image, corners_2d, color=(0, 255, 0))
    cv2.circle(image, tuple(center_2d[0].tolist()), point_radius, (255, 0, 0), 0)

    point_list = [tuple(centers_2d[i].tolist()) for i in range(3)]
    for point in point_list:
        cv2.circle(image, point, point_radius, (255, 0, 255), 0)

    line_color = (255, 255, 0)
    thickness = 1
    lineType = 4
    # for i in range(box2d[0], box2d[2]):
    #     for j in range(box2d[1], box2d[3]):

    i = np.random.randint(box2d[0], box2d[2])
    j = np.random.randint(box2d[1], box2d[3])

    image_ij = image.copy()
    ptStart = (i, j)
    point = np.array([i, j])
    # center -> point
    local_point = point - center_2d
    cv2.arrowedLine(image_ij, (center_2d[0, 0], center_2d[0, 1]), 
                (point[0], point[1]), line_color, thickness, lineType)
    # projection
    proj_local = vectors * np.sum(local_point * vectors, axis=1).reshape(-1, 1)
    proj_local_point = center_2d + proj_local
    cv2.arrowedLine(image_ij, (center_2d[0, 0], center_2d[0, 1]), 
                (int(proj_local_point[0, 0]), int(proj_local_point[0, 1])), line_color, thickness, lineType)

    bias = local_point - proj_local
    surface_proj = centers_2d + bias
    cv2.arrowedLine(image_ij, (centers_2d[0, 0], centers_2d[0, 1]), 
                (int(surface_proj[0, 0]), int(surface_proj[0, 1])), line_color, thickness, lineType)
    dis = surface_proj - point

    # for k in range(3):
    #     ptEnd = tuple(surface_proj[k].astype(np.int).tolist())
    #     cv2.arrowedLine(image_ij, ptStart, ptEnd, line_color, thickness, lineType)

    plt.figure()
    plt.imshow(image_ij)
    plt.show()

def show_image_with_boxes(image, cls_ids, target_center, box2d, corners_2d, reg_mask, offset_3D, down_ratio, pad_size, encoded_alphas, vis=True, index=[]):

    image = np.array(image)
    img2 = np.copy(image) # for 2d bbox
    img3 = np.copy(image) # for 3d bbox
    font = cv2.FONT_HERSHEY_SIMPLEX

    from utils.visualizer import Visualizer
    
    img2_vis = Visualizer(img2)

    ori_target_center = (target_center + offset_3D) * down_ratio

    box2d[:, 0::2] = box2d[:, 0::2] * down_ratio
    box2d[:, 1::2] = box2d[:, 1::2] * down_ratio
    center_2d = (box2d[:, :2] + box2d[:, 2:]) / 2

    id_to_cls = ['Car', 'Pedestrian', 'Cyclist']

    for idx in range(target_center.shape[0]):
        if reg_mask[idx] == 0:
            continue
            
        img2_vis.draw_box(box_coord=box2d[idx])

        alpha_regress = encoded_alphas[idx]
        text = '{} {} {:.1f}'.format(alpha_regress[0], alpha_regress[1], alpha_regress[2] / np.pi * 180)
        # img2_vis.draw_text(text=text, position=(int(box2d[idx, 0]), int(box2d[idx, 1])))

        # 8 x 3
        corners_2d_i = (corners_2d[idx][:, :2] + target_center[idx].reshape(1, 2)) * down_ratio
        img3 = draw_projected_box3d(img3, corners_2d_i, cls=id_to_cls[cls_ids[idx]], draw_corner=index)
        
        # target center and 3D center
        cv2.circle(img3, tuple(center_2d[idx].astype(np.int)), 4, (255, 0, 0), -1)
        cv2.circle(img3, tuple(ori_target_center[idx].astype(np.int)), 4, (0, 255, 255), -1)

    img2 = img2_vis.output.get_image()
    stacked_img = np.vstack((img2, img3))

    # if vis:
    #     plt.figure(figsize=(10, 6))
    #     plt.imshow(stacked_img)
    #     plt.show()

    return stacked_img

def show_edge_heatmap(img, edge_heatmap, interp_edge_indices, output_size):
    # output_size: w, h
    resized_img = img.resize(output_size)
    interp_edge_indices_int = interp_edge_indices.round().astype(np.int)
    full_edgemap = np.zeros((output_size[1], output_size[0]), dtype=np.float32)
    full_edgemap[interp_edge_indices_int[:, 1], interp_edge_indices_int[:, 0]] = edge_heatmap[0]

    plt.figure()
    plt.subplot(211)
    plt.imshow(resized_img)
    plt.subplot(212)
    plt.imshow(full_edgemap)
    plt.show()

def show_heatmap(img, heat_map, classes=['Car', 'Pedestrian', 'Cyclist'], index=None):
    colors = [(0, 1, 0), (1, 0, 0), (0, 0, 1)]
    ignored_color = np.array([1, 1, 1])

    resized_img = img.resize((heat_map.shape[2], heat_map.shape[1]))
    mix_img = np.array(resized_img) / 255 
    all_heat_img = np.zeros_like(mix_img)
    
    for k in range(len(classes)):
        heat_map_k = heat_map[k, :, :]
        ignored_class_map = np.zeros_like(mix_img)
        ignored_class_map[heat_map_k == -1] = ignored_color
        heat_map_k[heat_map_k == -1] = 0
        class_map = heat_map_k[..., np.newaxis] * np.array(colors[k]).reshape(1, 1, 3) + ignored_class_map
        
        mix_img += class_map
        all_heat_img += class_map
    
    import imageio
    imageio.imsave('/data/lpc_data/test/heatmap_img_{}.png'.format(index), mix_img)
    # print(index)

    # plt.figure(figsize=(10, 6))
    # plt.subplot(311)
    # plt.imshow(img)
    # plt.axis('off')
    # plt.subplot(312)
    # plt.imshow(mix_img)
    # plt.axis('off')
    # plt.subplot(313)
    # plt.imshow(all_heat_img)
    # plt.axis('off')
    # plt.show()

def get_average(list):
    sum=0
    for item in list:
        sum+=item
    return sum/len(list)


def calculate_depth_error(objs,objs_det,channel=30):
    gt_depth = [[[] for i in range(channel) ] for j in range(3)]
    absolute_error=[[[] for i in range(channel) ] for j in range(3)]
    relative_error=[[[] for i in range(channel) ] for j in range(3)]
    shape_error=[[[] for i in range(channel) ] for j in range(3)]
    yaw_error=[[[] for i in range(channel) ] for j in range(3)]

    for j, obj in enumerate(objs):
        #车道分类
        if -5.7 <= obj.t[0] < -1.9:
            index_x = 0
        elif -1.9 <= obj.t[0] <= 1.9:
            index_x = 1
        elif 1.9 < obj.t[0] <= 5.7:
            index_x = 2
        else:
            # print('object out of -5.7~5.7')
            continue
        for i, obj_det in enumerate(objs_det):
            if obj.id!=0 and obj_det.id!=0 and obj.id==obj_det.id:
                index_z=int((obj.t[2]-obj.t[2]%10)/10)
                if index_z > channel-1:
                    continue
                #print(index_x," : ",index_z)
                gt_depth[index_x][index_z].append(round(obj.t[2],3))
                absolute_error[index_x][index_z].append(round(abs(obj_det.t[2]- obj.t[2]),3))
                relative_error[index_x][index_z].append(round(abs(obj_det.t[2]- obj.t[2])/obj.t[2],3))
                if round(abs(obj_det.t[2]- obj.t[2])/obj.t[2],3) > 1.0:
                    print('weird relative error',relative_error[index_x][index_z])
                    print('single pic: gt_depth',gt_depth,'absolute_error',absolute_error)
                shape_error[index_x][index_z].append(round(mAOE(obj.s,obj_det.s),3)) # shape error
                yaw_error[index_x][index_z].append(round(mASE(obj.ry,obj_det.ry),3)) # yaw error
    
    # print('single pic: gt_depth',gt_depth,'absolute_error',absolute_error,'relative_error',relative_error)
    # print('shape_error',shape_error)
    # print('yaw_error',yaw_error)
    return gt_depth,absolute_error,relative_error,shape_error,yaw_error

def toxlxs_3d(savepath,depth_error,shape_error,yaw_error,channel):
    book = xlwt.Workbook(encoding='utf-8',style_compression=0)
    listname = '3D-depth-honghu'
    sheet = book.add_sheet(listname,cell_overwrite_ok=True)
    x = 0
    for i in range(channel):
        sheet.write(0 , i, str(x))
        x+=10
    sheet.write(1, 0, str('3D目标深度误差百分比'))
    for j in range(3):
        for i in range(channel):
            sheet.write(j+2,i,str(depth_error[j][i]))
    sheet.write(5, 0, str('3D目标尺寸误差百分比'))
    for i in range(3):
        for j in range(channel):
            sheet.write(i+6,j,str(shape_error[i][j]))
    sheet.write(9, 0, str('3D目标yaw误差'))
    for i in range(3):
        for j in range(channel):
            sheet.write(i+10,j,str(yaw_error[i][j]))
    book.save(savepath)

def sum_list(list,channel):
    new_list = [[[] for i in range(channel) ] for j in range(3)]
    for j in range(3):
        for i in range(channel):
            new_list[j][i].append(sum(list[j][i]))
    return new_list


def mean_list(list,channel):
    new_list = [[[] for i in range(channel) ] for j in range(3)]
    for j in range(3):
        for i in range(channel):
            if len(list[j][i])==0:
                pass
            else:
                # print('dscfhchsdgfksd',sum(list[j][i]),len(list[j][i]))
                new_list[j][i].append(np.round(sum(list[j][i])/len(list[j][i]),3))
        #print(sum(list[j][0])," : ",len(list[j][i]))
    # print(new_list)
    return new_list

def align_center_corners3d(whl,ry=0):
    """
    generate corners3d representation for this object
    :return corners_3d: (8, 3) corners of box3d in camera coord
    """
    l, h, w = whl[2],whl[1],whl[0]
    x_corners = [l / 2, l / 2, -l / 2, -l / 2, l / 2, l / 2, -l / 2, -l / 2]
    y_corners = [0, 0, 0, 0, -h, -h, -h, -h]
    z_corners = [w / 2, -w / 2, -w / 2, w / 2, w / 2, -w / 2, -w / 2, w / 2]

    R = np.array([[np.cos(ry), 0, np.sin(ry)],
                    [0, 1, 0],
                    [-np.sin(ry), 0, np.cos(ry)]])

    corners3d = np.vstack([x_corners, y_corners, z_corners])  # (3, 8)
    corners3d = np.dot(R, corners3d).T
    corners3d = corners3d

    return corners3d

# def get_iou_3d(pred_corners, target_corners):
#     print(pred_corners.shape)
#     """ 
#     :param corners3d: (N, 8, 3) in rect coords  
#     :param query_corners3d: (N, 8, 3)   
#     :return: IoU 
#     """
#     A, B = pred_corners, target_corners
#     # N = A.shape[0]

#     # init output
#     # iou3d = np.zeros((N))

#     # for height overlap, since y face down, use the negative y
#     min_h_a = - A[0:4, 1].sum(axis=0) / 4.0
#     max_h_a = - A[4:8, 1].sum(axis=0)/ 4.0
#     min_h_b = - B[0:4, 1].sum(axis=0)/ 4.0
#     max_h_b = - B[4:8, 1].sum(axis=0)/ 4.0

#     # x-z plane overlap
#     # for i in range(N):
#          # overlap in height
#     h_max_of_min = np.max([min_h_a, min_h_b])
#     h_min_of_max = np.min([max_h_a, max_h_b])
#     h_overlap = np.max([0, h_min_of_max - h_max_of_min])
#     bottom_a, bottom_b = Polygon(A[ 0:4, [0, 2]].T), Polygon(B[ 0:4, [0, 2]].T)

#     if bottom_a.is_valid and bottom_b.is_valid:
#         # check is valid,  A valid Polygon may not possess any overlapping exterior or interior rings.
#         bottom_overlap = bottom_a.intersection(bottom_b).area
#     else:
#         bottom_overlap = 0
        
#     overlap3d = bottom_overlap * h_overlap
#     union3d = bottom_a.area * (max_h_a- min_h_a) + bottom_b.area * (max_h_b- min_h_b) - overlap3d
#     iou3d= overlap3d / union3d

#     return iou3d

def get_iou_3d(pred_corners, target_corners):
    """ 
    :param corners3d: (N, 8, 3) in rect coords  
    :param query_corners3d: (N, 8, 3)   
    :return: IoU 
    """
    A, B = pred_corners, target_corners
    N = A.shape[0]

    # init output
    iou3d = np.zeros((N))

    # for height overlap, since y face down, use the negative y
    min_h_a = - A[:, 0:4, 1].sum(axis=1) / 4.0
    max_h_a = - A[:, 4:8, 1].sum(axis=1)/ 4.0
    min_h_b = - B[:, 0:4, 1].sum(axis=1)/ 4.0
    max_h_b = - B[:, 4:8, 1].sum(axis=1)/ 4.0

    # x-z plane overlap
    for i in range(N):
         # overlap in height
        h_max_of_min = np.max([min_h_a[i], min_h_b[i]])
        h_min_of_max = np.min([max_h_a[i], max_h_b[i]])
        h_overlap = np.max([0, h_min_of_max - h_max_of_min])
        bottom_a, bottom_b = Polygon(A[i, 0:4, [0, 2]].T), Polygon(B[i, 0:4, [0, 2]].T)

        if bottom_a.is_valid and bottom_b.is_valid:
            # check is valid,  A valid Polygon may not possess any overlapping exterior or interior rings.
            bottom_overlap = bottom_a.intersection(bottom_b).area
        else:
            bottom_overlap = 0
            
        overlap3d = bottom_overlap * h_overlap
        union3d = bottom_a.area * (max_h_a[i] - min_h_a[i]) + bottom_b.area * (max_h_b[i] - min_h_b[i]) - overlap3d
        iou3d[i] = overlap3d / union3d
    
    return iou3d[0]

# calculate mean shape error
def mAOE(bbox3d_gt, bbox3d_pred):# bbox3d_gt : n × 3 ， [w,h,l]
    corners3d_gt = np.array([align_center_corners3d(bbox3d_gt)])
    corners3d_pred = np.array([align_center_corners3d(bbox3d_pred)])
    shape_error = 1- get_iou_3d(corners3d_gt, corners3d_pred)
    return shape_error

# calculate mean yaw error  (unite :degree [0,180])
def mASE(bbox3d_gt, bbox3d_pred):  # bbox3d_gt : n × 1 ， [yaw]
    if abs(bbox3d_gt)>3.15:
        bbox3d_gt = maxus2kitti(bbox3d_gt)
    yaw_error = bbox3d_gt - bbox3d_pred
    mean_yaw_error = np.mean(np.abs(yaw_error))/3.1415*180
    if mean_yaw_error>1000:
        print("error yaw :",bbox3d_gt)
        print("error yaw pred: ",bbox3d_pred)
    return mean_yaw_error