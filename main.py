# -*- coding: utf-8 -*-
import time
# import re
# import sys
import cv2
import numpy as np
from core.cv.match_template import find_template
from core.run import Android
# from loguru import logger
from coordinate import Anchor, Point, Size, Rect

# a = Android(device_id='emulator-5562', cap_method='minicap', touch_method='adbtouch')

# from core.cv.sift import SIFT
# im_source = cv_imread('./tmp/iphone.png')
# im_search = cv_imread('./tmp/编队.png')
# sift = SIFT()
# sift.find_sift_narrow(im_search=im_search, im_source=im_source)
# sift.find_sift(im_search=im_search, im_source=im_source)

from core.cv.base_image import image
from core.cv.utils import bytes_2_img
a = image('iphone.png')
