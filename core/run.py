#! usr/bin/python
# -*- coding:utf-8 -*-
import time
from core.adb import ADB
from core.cap_methods.minicap import Minicap
from core.touch_methods.event_touch import Touch as EVENTTOUCH
from core.touch_methods.minitouch import Minitouch
from core.touch_methods.maxtouch import Maxtouch
from core.cap_methods.adbcap import AdbCap
from core.constant import TOUCH_METHOD, CAP_METHOD, SDK_VERISON_ANDROID10
from core.cap_methods.Javecap import Javacap
from core.rotation import Rotation
from core.utils.base import initLogger
from baseImage import IMAGE as Image
from core.constant import ADB_CAP_REMOTE_PATH
from loguru import logger

# 初始化loguru
initLogger()


class Android(object):
    def __init__(self, device_id=None, adb_path=None, host='127.0.0.1', port=5037,
                 touch_method: str = TOUCH_METHOD.MINITOUCH,
                 cap_method: str = CAP_METHOD.MINICAP):
        # init adb
        self.adb = ADB(device_id, adb_path, host, port)
        self._display_info = {}
        self.tmp_image = Image(path=ADB_CAP_REMOTE_PATH.format(self.adb.get_device_id(decode=True)))
        self.sdk_version = self.adb.sdk_version()
        # init components
        self.cap_method = cap_method
        self.touch_method = touch_method
        if self.sdk_version >= SDK_VERISON_ANDROID10 and self.touch_method == TOUCH_METHOD.MINITOUCH:
            self.touch_method = TOUCH_METHOD.MAXTOUCH
        if self.touch_method == TOUCH_METHOD.MINITOUCH:
            self.minitouch = Minitouch(self.adb)
        #  由于在一些设备上minicap无法启动,因此做了一些限制 error: have different types
        if self.cap_method == CAP_METHOD.MINICAP:
            self.minicap = Minicap(self.adb)
        self.javacap = Javacap(self.adb)
        self.adbcap = AdbCap(self.adb)
        self.maxtouch = Maxtouch(self.adb)
        self.EVENTTOUCH = EVENTTOUCH(self.adb)
        self.rotation_watcher = Rotation(self.adb)
        self.rotation_watcher.start()
        self._register_rotation_watcher()

    def screenshot(self):
        stamp = time.time()
        img_data = None
        if self.cap_method == CAP_METHOD.MINICAP:
            img_data = self.minicap.get_frame()
        elif self.cap_method == CAP_METHOD.JAVACAP:
            img_data = self.javacap.get_frame()
        elif self.cap_method == CAP_METHOD.ADBCAP:
            img_data = self.adbcap.get_frame()
        # 图片写入到缓存中
        self.tmp_image.imwrite(img_data)
        logger.info("screenshot time={:.2f}ms,size=({},{})", (time.time() - stamp) * 1000, *self.tmp_image.size)
        return self.tmp_image

    def down(self, x: int, y: int, index: int = 0, pressure: int = 50):
        if self.touch_method == TOUCH_METHOD.MINITOUCH:
            return self.minitouch.down(x, y, index, pressure)
        elif self.touch_method == TOUCH_METHOD.ADBTOUCH:
            return self.EVENTTOUCH.down(x, y, index)

    def up(self, x: int, y: int, index: int = 0):
        if self.touch_method == TOUCH_METHOD.MINITOUCH:
            return self.minitouch.up(x, y, index)
        elif self.touch_method == TOUCH_METHOD.ADBTOUCH:
            return self.EVENTTOUCH.up(x, y, index)

    def sleep(self, duration):
        if self.touch_method == TOUCH_METHOD.MINITOUCH:
            return self.minitouch.sleep(duration)
        elif self.touch_method == TOUCH_METHOD.ADBTOUCH:
            return self.EVENTTOUCH.sleep(duration)

    def click(self, x: int, y: int, index: int = 0, duration=0.1):
        logger.info("[{}]index={}, x={}, y={}", self.touch_method, index, x, y)
        if self.touch_method == TOUCH_METHOD.MINITOUCH:
            return self.minitouch.click(x, y, index=index, duration=duration)
        elif self.touch_method == TOUCH_METHOD.MAXTOUCH:
            return self.maxtouch.click(x, y, index=index, duration=duration)
        elif self.touch_method == TOUCH_METHOD.ADBTOUCH:
            return self.EVENTTOUCH.click(x, y, index=index, duration=duration)

    def display_info(self):
        if not self._display_info:
            self._display_info = self.get_display_info()

    def get_display_info(self):
        if self.cap_method == CAP_METHOD.MINICAP:
            try:
                return self.minicap.get_display_info()
            except RuntimeError:
                # Even if minicap execution fails, use adb instead
                return self.adb.get_display_info()
        return self.adb.get_display_info()

    def _get_touch_method(self):
        if self.touch_method == TOUCH_METHOD.MAXTOUCH:
            return self.maxtouch
        elif self.touch_method == TOUCH_METHOD.MINITOUCH:
            return self.minitouch
        elif self.touch_method == TOUCH_METHOD.ADBTOUCH:
            return self.EVENTTOUCH

    def _get_cap_func(self):
        if self.cap_method == CAP_METHOD.MINICAP:
            return self.minicap
        elif self.cap_method == CAP_METHOD.JAVACAP:
            return self.javacap
        elif self.cap_method == CAP_METHOD.ADBCAP:
            return self.adb.screenshot

    def _register_rotation_watcher(self):
        self.rotation_watcher.reg_callback(lambda x: self._get_touch_method().update_rotation(x * 90))
        if self.cap_method == CAP_METHOD.MINICAP:
            self.rotation_watcher.reg_callback(lambda x: self.minicap.update_rotation(x * 90))
