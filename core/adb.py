#! usr/bin/python
# -*- coding:utf-8 -*-
import os
import platform
import random
import re
import socket
import subprocess
import sys
import time
import warnings
from typing import Union, Tuple

import numpy as np
from loguru import logger

from core.utils.base import SUBPROCESS_FLAG, pprint
from core.constant import DEFAULT_ADB_PATH, SHELL_ENCODING, ADB_CAP_NAME_RAW, ADB_CAP_REMOTE_RAW_PATH, \
    ADB_CAP_LOCAL_PATH, ADB_CAP_REMOTE_PATH
from core.error import AdbError
from core.utils.snippet import split_cmd, split_process_status, get_std_encoding


class _ADB(object):
    """adb object class"""

    def __init__(self, device_id: str = None, adb_path: str = None, host='127.0.0.1', port=5037):
        self.device_id = device_id
        self.adb_path = adb_path or self.builtin_adb_path()
        self._set_cmd_options(host, port)
        self.connect()
        self._event_path = None  # event信息
        self._display_info = {}
        self._line_breaker = None
        # 截图文件名字
        self._cap_name = ADB_CAP_NAME_RAW.format(self.get_device_id(decode=True))
        # 截图在手机上的路径
        self._cap_local_path = ADB_CAP_LOCAL_PATH.format(self._cap_name)
        # 截图png存放到工程的路径
        self._cap_remote_path = ADB_CAP_REMOTE_PATH.format(self.get_device_id(decode=True))
        # raw临时文件存放到工程的路径
        self._cap_raw_remote_path = ADB_CAP_REMOTE_RAW_PATH.format(self._cap_name)
        # 已经使用的端口
        self._sdk_version = self.sdk_version()

    @staticmethod
    def builtin_adb_path() -> str:
        """adb路径"""
        system = platform.system()
        machine = platform.machine()
        adb_path = DEFAULT_ADB_PATH.get('{}-{}'.format(system, machine))
        if not adb_path:
            adb_path = DEFAULT_ADB_PATH.get(system)
        if not adb_path:
            raise RuntimeError("No adb executable supports this platform({}-{}).".format(system, machine))

        # overwrite uiautomator adb
        if "ANDROID_HOME" in os.environ:
            del os.environ["ANDROID_HOME"]
        return adb_path

    def _set_cmd_options(self, host: str, port: int):
        """
        设置adb服务器
        Args:
            host: adb路径
            port: adb端口号
        Returns:
            None
        """
        self.host = host
        self.port = port
        self.cmd_options = [self.adb_path]
        if self.host not in ("localhost", "127.0.0.1"):
            self.cmd_options += ['-H', self.host]
        if self.port != 5037:
            self.cmd_options += ['-P', str(self.port)]

    def start_server(self):
        """
        command 'adb start-server'

        :return: None
        """
        return self.cmd('start-server', devices=False)

    def kill_server(self):
        """
        command 'adb kill-server'

        :return: None
        """
        return self.cmd('kill-server', devices=False)

    def start_cmd(self, cmds: Union[list, str], devices: bool = True) -> subprocess.Popen:
        """
        用cmds创建一个subprocess.Popen

        Args:
            cmds: 需要运行的参数,可以是list,str
            devices: 如果为True,则需要指定device-id,命令中会传入-s
        Returns:
            subprocess.Popen
        """
        cmds = split_cmd(cmds)
        if devices:
            if not self.device_id:
                raise logger.error('please set device_id first')
            cmd_options = self.cmd_options + ['-s', self.device_id]
            logger.debug('adb -s {} {}', self.device_id, " ".join(cmds))
            # logger.debug('adb {}', " ".join(cmds))
        else:
            cmd_options = self.cmd_options
            logger.debug('adb {}', " ".join(cmds))

        cmds = cmd_options + cmds
        proc = subprocess.Popen(
            cmds,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=SUBPROCESS_FLAG
        )
        return proc

    @staticmethod
    def close_proc_pipe(proc: subprocess.Popen) -> None:
        """
        关闭stdin,stdout,stderr流对象

        Args: 
            proc: 选择关闭的Popen对象

        Returns:
             None 
        """""

        def close_pipe(pipe):
            if pipe:
                pipe.close()

        close_pipe(proc.stdin)
        close_pipe(proc.stdout)
        close_pipe(proc.stderr)

    def cmd(self, cmds: Union[list, str], devices: bool = True, ensure_unicode: bool = True, timeout: int = None,
            skip_error: bool = False) -> bytes:
        """
        用cmds创建adb命令,并且返回stdout

        Args:
            cmds: 需要运行的参数,可以是list,str
            devices: 如果为True,则需要指定device-id,命令中会传入-s
            ensure_unicode: 是否解码stdout,stderr
            timeout: 设置命令超时时间
            skip_error: 是否跳过报错

        Returns:
            返回命令结果stdout
        """
        proc = self.start_cmd(cmds, devices)
        if timeout:
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                logger.error("Command {cmd} time out after {timeout} seconds: stdout['{stdout}'], stderr['{stderr}']",
                             cmd=proc.args, timeout=timeout,
                             stdout=stdout, stderr=stderr)
                raise
        else:
            stdout, stderr = proc.communicate()
        if ensure_unicode:
            stdout = stdout.decode(get_std_encoding(stdout))
            stderr = stderr.decode(get_std_encoding(stderr))

        if proc.returncode > 0:
            # adb error
            # logger.error("adb connection {stdout} {stderr}", stdout=stdout, stderr=stderr)
            if not skip_error:
                raise AdbError(stdout, stderr, cmds)
        return stdout

    def devices(self, state: bool = None) -> list:
        """
        command adb devices

        Args:
            state: 过滤属性比如: 'device', 'offline'
        Returns:
            返回adb设备列表 List
        """
        patten = re.compile(r'^[\w\d.:-]+\t[\w]+$')
        device_list = []
        # self.start_server()
        output = self.cmd("devices", devices=False)
        for line in output.splitlines():
            line = line.strip()
            if not line or not patten.match(line):
                continue
            serialno, cstate = line.split('\t')
            if state and cstate != state:
                continue
            device_list.append((serialno, cstate))
        return device_list

    def connect(self, force=False):
        """
        command adb connect

        Returns:
             None
        """
        if self.device_id and ':' in self.device_id and (force or self.get_status() != 'devices'):
            connect_result = self.cmd("connect %s" % self.device_id, devices=False)
            logger.info(connect_result.rstrip())

    def disconnect(self):
        """
        command adb disconnect

        Returns:
             None
        """
        if ':' in self.device_id:
            self.cmd("disconnect %s" % self.device_id, devices=False)
            logger.info("disconnect to %s" % self.device_id)

    def get_status(self):
        """
        command adb -s <devices_id> get-state
        :return:
        """
        proc = self.start_cmd('get-state')
        stdout, stderr = proc.communicate()

        stdout = stdout.decode(get_std_encoding(sys.stdout))
        stderr = stderr.decode(get_std_encoding(sys.stdout))

        if proc.returncode == 0:
            return stdout.strip()
        elif "not found" in stderr:
            return None
        else:
            raise AdbError(stdout, stderr, ['get-state'])

    @property
    def line_breaker(self):
        """
        Set carriage return and line break property for various platforms and SDK versions
        Returns:
            carriage return and line break string
        """
        if not self._line_breaker:
            if self._sdk_version >= 24:
                line_breaker = os.linesep
            else:
                line_breaker = '\r' + os.linesep
            self._line_breaker = line_breaker.encode("ascii")
        return self._line_breaker

    def start_shell(self, cmds: Union[list, str]) -> start_cmd:
        cmds = ['shell'] + split_cmd(cmds)
        return self.start_cmd(cmds)

    def raw_shell(self, cmds: Union[list, str], ensure_unicode: bool = True, skip_error: bool = False):
        cmds = ['shell'] + split_cmd(cmds)
        stdout = self.cmd(cmds, ensure_unicode=False, skip_error=skip_error)
        if not ensure_unicode:
            return stdout
        try:
            return stdout.decode(SHELL_ENCODING)
        except UnicodeDecodeError:
            logger.error('shell output decode {} fail. repr={}', SHELL_ENCODING, repr(stdout))
            return str(repr(stdout))

    def shell(self, cmd: Union[list, str]) -> str:
        if self._sdk_version < 25:
            # sdk_version < 25, adb shell 不返回错误
            # https://issuetracker.google.com/issues/36908392
            cmd = split_cmd(cmd) + [";", "echo", "---$?---"]
            out = self.raw_shell(cmd).rstrip()
            m = re.match("(.*)---(\d+)---$", out, re.DOTALL)
            if not m:
                warnings.warn("return code not matched")
                stdout = out
                returncode = 0
            else:
                stdout = m.group(1)
                returncode = int(m.group(2))
            if returncode > 0:
                raise logger.error('adb shell error')
            return stdout
        else:
            try:
                out = self.raw_shell(cmd)
            except AdbError as err:
                raise logger.error("stdout={},stderr={}", err.stdout, err.stderr)
            else:
                return out

    def getprop(self, key, strip=True):
        prop = self.raw_shell(['getprop', key])
        if strip:
            prop = prop.rstrip()
        return prop

    def forward(self, local: str, remote: str, no_rebind: bool = True):
        """
        command adb forward

        Args:
            local: 要转发的本地端口 tcp:<local>

            remote: 要与local绑定的设备端口 localabstract:{remote}"`
        :return:
            None
        """
        is_use, index = self._local_in_forwards(local, remote)
        if not is_use:
            cmds = ['forward']
            if no_rebind:
                cmds += ['--no-rebind']
            self.cmd(cmds + [local, remote])
            logger.debug('forward {} {}', local, remote)
        else:
            forward_local_using = self.get_forwards()
            logger.info('{} {} has been forward', forward_local_using[index]['local'],
                        forward_local_using[index]['remote'])

    def get_available_forward_local(self) -> int:
        """
        获取一个可用端口
        :return:
            port
        """
        sock = socket.socket()
        port = random.randint(11111, 20000)
        result = False
        try:
            sock.bind((self.host, port))
            result = True
            # logger.debug('port:{} can use'.format(port))
        except socket.error:
            logger.debug('port:{} is in use'.format(port))
        sock.close()
        if not result:
            return self.get_available_forward_local()
        return port

    def _local_in_forwards(self, local: str = None, remote: str = None) -> Union[Tuple[bool, int], Tuple[bool, None]]:
        """
        检查local是否已经启用

        :return:
            bool, if True return index in _forward_local_using
        """
        l = self.get_forwards()
        for i, value in enumerate(l):
            if local:
                if value['local'] == local:
                    return True, i
            if remote:
                if value['remote'] == remote:
                    return True, i
        return False, None

    def abi_version(self):
        """ get abi (application binary interface) """
        abi = self.getprop('ro.product.cpu.abi')
        logger.info('device {} abi is {}'.format(self.device_id, abi).rstrip('\r\n'))
        return abi

    def sdk_version(self):
        """ get sdk version """
        sdk = self.getprop('ro.build.version.sdk')
        logger.info('device {} sdk is {}'.format(self.device_id, sdk).rstrip('\r\n'))
        return int(sdk)

    def check_file(self, path: str, name: str) -> bool:
        """
        command adb shell find 'name' in the 'path'

        Args:
            path: 在设备上的路径

            name: 需要检查的文件
        :return:
            bool
        """
        return bool(self.raw_shell(['find', path, '-name', name]))

    def get_process_status(self, pid: int = None, name: str = None) -> list:
        """
        adb shell ps
        获取应用状态

        Args:
            pid: 按照pid寻找
            name: 通过grep寻找匹配的name(并不是精准寻找,只要有匹配的项都会返回)
        :return:
            list 每一项都包含了ps信息
        """
        if pid:
            shell = ['ps -x', str(pid)]
        elif name:
            shell = "ps | grep -w \"{}\"".format(name)
        else:
            shell = 'ps'
        out = self.raw_shell(shell, skip_error=True)
        out = split_process_status(out)
        if not out:
            logger.error('{}: {} is not started', pid and 'pid' or 'name', pid and pid or name)
        return out

    def kill_process(self, pid: int = None, name: str = None):
        """
        command adb shell kill [pid]
        :param name: 需要杀死的进程名
        :param pid: 需要杀死的进程pid
        :return:
            None
        """
        if pid:
            out = self.get_process_status(pid=pid)
            if out:
                pid = out[0]['PID']
            else:
                return False
        elif name:
            out = self.get_process_status(name=name)
            if out:
                if len(out) > 1:
                    logger.info('匹配到多个进程')
                pid = out[0]['PID']
            else:
                return False
        self.start_shell(['kill', str(pid)])
        logger.info('{} PID:{} NAME:{} is kill', self.device_id, pid, out[0]['NAME'])

    def get_device_id(self, decode: bool = False) -> str:
        return decode and self.device_id.replace(':', '_') or self.device_id

    def set_forward(self, remote: str):
        """
        通过get_available_forward_local获取可用端口,并与remote绑定

        Args:
            remote: 要与local绑定的设备端口 localabstract:{remote}"

        :return:
            localport:要转发的本地端口 tcp:<local>
            remote:要与local绑定的设备端口 localabstract:{remote}"`
        """
        localport = self.get_available_forward_local()
        self.forward('tcp:%s' % localport, remote)
        return localport, remote

    def get_forward_port(self, remote: str):
        """获取开放端口的端口号"""
        index = self._local_in_forwards(remote='localabstract:%s' % remote)
        if not index[0]:
            logger.error('No port corresponding to remote was found')
            return None
        return int(re.compile(r'tcp:(\d+)').findall(self.get_forwards()[index[1]]['local'])[0])

    def remove_forward(self, local=None):
        """
        运行adb forward -- remove
        :param local:
            tcp port,如不填写则清楚所以绑定
        :return:
            None
        """
        if local:
            cmds = ['forward', '--remove', local]
        else:
            cmds = ['forward', '--remove-all']
        self.cmd(cmds)

    def get_forwards(self) -> list:
        """
        command adb forward --list

        :return:
            返回一个包含占用信息的列表,每个包含键值local和remote
        """
        l = []
        out = self.cmd(['forward', '--list'], devices=False, skip_error=True)
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            cols = line.split()
            if len(cols) != 3:
                continue
            device_id, local, remote = cols
            l.append({'local': local, 'remote': remote})
        return l

    def getMaxXY(self):
        ret = self.shell('getevent -p').split('\n')
        max_x, max_y = None, None
        for i in ret:
            if i.find("0035") != -1:
                patten = re.compile(r'max [0-9]+')
                ret = patten.search(i)
                if ret:
                    max_x = int(ret.group(0).split()[1])

            if i.find("0036") != -1:
                patten = re.compile(r'max [0-9]+')
                ret = patten.search(i)
                if ret:
                    max_y = int(ret.group(0).split()[1])
        return max_x, max_y

    def getPhysicalDisplayInfo(self):
        """
        Get value for display dimension and density from `mPhysicalDisplayInfo` value obtained from `dumpsys` command.

        Returns:
            physical display info for dimension and density

        """
        phyDispRE = re.compile(
            '.*PhysicalDisplayInfo{(?P<width>\d+) x (?P<height>\d+), .*, density (?P<density>[\d.]+).*')
        out = self.raw_shell('dumpsys display')
        m = phyDispRE.search(out)
        if m:
            displayInfo = {}
            for prop in ['width', 'height']:
                displayInfo[prop] = int(m.group(prop))
            for prop in ['density']:
                # In mPhysicalDisplayInfo density is already a factor, no need to calculate
                displayInfo[prop] = float(m.group(prop))
            return displayInfo

        # This could also be mSystem or mOverscanScreen
        phyDispRE = re.compile('\s*mUnrestrictedScreen=\((?P<x>\d+),(?P<y>\d+)\) (?P<width>\d+)x(?P<height>\d+)')
        # This is known to work on older versions (i.e. API 10) where mrestrictedScreen is not available
        dispWHRE = re.compile('\s*DisplayWidth=(?P<width>\d+) *DisplayHeight=(?P<height>\d+)')
        out = self.raw_shell('dumpsys window')
        m = phyDispRE.search(out, 0)
        if not m:
            m = dispWHRE.search(out, 0)
        if m:
            displayInfo = {}
            for prop in ['width', 'height']:
                displayInfo[prop] = int(m.group(prop))
            for prop in ['density']:
                d = self._getDisplayDensity(None, strip=True)
                if d:
                    displayInfo[prop] = d
                else:
                    # No available density information
                    displayInfo[prop] = -1.0
            return displayInfo

        # gets C{mPhysicalDisplayInfo} values from dumpsys. This is a method to obtain display dimensions and density
        phyDispRE = re.compile('Physical size: (?P<width>\d+)x(?P<height>\d+).*Physical density: (?P<density>\d+)',
                               re.S)
        m = phyDispRE.search(self.raw_shell('wm size; wm density'))
        if m:
            displayInfo = {}
            for prop in ['width', 'height']:
                displayInfo[prop] = int(m.group(prop))
            for prop in ['density']:
                displayInfo[prop] = float(m.group(prop))
            return displayInfo

        return {}

    def _getDisplayDensity(self, key, strip=True):
        """
        Get display density
        Args:
            key:
            strip: strip the output
        Returns:
            display density
        """
        BASE_DPI = 160.0
        d = self.getprop('ro.sf.lcd_density', strip)
        if d:
            return float(d) / BASE_DPI
        d = self.getprop('qemu.sf.lcd_density', strip)
        if d:
            return float(d) / BASE_DPI
        return -1.0

    def getDisplayOrientation(self):
        """
        Another way to get the display orientation, this works well for older devices (SDK version 15)

        Returns:
            display orientation information

        """
        # another way to get orientation, for old sumsung device(sdk version 15) from xiaoma
        SurfaceFlingerRE = re.compile('orientation=(\d+)')
        output = self.shell('dumpsys SurfaceFlinger')
        m = SurfaceFlingerRE.search(output)
        if m:
            return int(m.group(1))

        # Fallback method to obtain the orientation
        # See https://github.com/dtmilano/AndroidViewClient/issues/128
        surfaceOrientationRE = re.compile('SurfaceOrientation:\s+(\d+)')
        output = self.shell('dumpsys input')
        m = surfaceOrientationRE.search(output)
        if m:
            return int(m.group(1))

        # We couldn't obtain the orientation
        warnings.warn("Could not obtain the orientation, return 0")
        return 0


class _Device(_ADB):
    """这里会写上一些常用的功能接口"""

    def screenshot(self, Rect: Tuple[int, int, int, int] = None):
        """
        adb 截图
        :param Rect: 顶点坐标x,y。截取长宽width,height
        """
        # 截取临时文件raw
        local_path = self._cap_local_path
        raw_remote_path = self._cap_raw_remote_path

        self.raw_shell(['screencap', local_path])
        self.start_shell(['chmod', '755', local_path])
        self.pull(local_path, raw_remote_path)
        # readRaw
        # read size
        img_data = np.fromfile(raw_remote_path, dtype=np.uint16)
        width, height = img_data[2], img_data[0]
        # read raw
        img_data = np.fromfile(raw_remote_path, dtype=np.uint8)
        img_data = img_data[slice(12, len(img_data))]
        if Rect:
            if Rect[0] > height or Rect[1] > width or Rect[0] + Rect[2] > height or Rect[1] + Rect[3] > width:
                raise OverflowError('Rect不能超出屏幕 {}'.format(Rect))
            index = Rect[0] * 4 + Rect[1] * height * 4  # 从图片左上角开始,y计算公式y*height*4,x计算公式x*4, 4表示4通道
            end = (Rect[0] + Rect[2]) * 4 + (Rect[1] + Rect[3] - 1) * height * 4
            img_data = img_data[index: end]
            img_data = img_data.reshape(Rect[3], Rect[2], 4)
        else:
            img_data = img_data.reshape(width, height, 4)
        img_data = img_data[:, :, ::-1][:, :, 1:4]  # imgData中rgbA转为Abgr,并截取bgr
        # 删除raw临时文件
        os.remove(raw_remote_path)
        return img_data

    def push(self, local, remote):
        """
        运行adb push
        :param local:
            需要发送的文件路径
        :param remote:
            发送到设备上的路径
        :return:
            None
        """
        if not os.path.isfile(local):
            raise RuntimeError("file: %s does not exists" % (repr(local)))
        self.cmd(["push", local, remote], ensure_unicode=False)

    def pull(self, remote, local):
        """
        运行adb pull
        :param remote:
            设备上的路径
        :param local:
            pull到本地的路径
        :return:
            None
        """
        self.cmd(["pull", remote, local], ensure_unicode=False)

    def install_app(self, filepath, replace=False, install_options=None):
        """
        command 'adb install'
        :param filepath: 文件路径
        :param replace: 是否覆盖安装,默认否
        :param install_options:
                e.g.["-t",  # allow test packages
                    "-l",  # forward lock application,
                    "-s",  # install application on sdcard,
                    "-d",  # allow version code downgrade (debuggable packages only)
                    "-g",  # grant all runtime permissions
                ]
        :return: command stdout
        """
        if not os.path.isfile(filepath):
            raise RuntimeError("file: %s does not exists" % (repr(filepath)))

        if not install_options or type(install_options) != list:
            install_options = []
            if replace:
                install_options.append('-r')
        cmds = ["install", ] + install_options + [filepath]
        out = self.cmd(cmds)
        logger.info('install app:%s' % filepath)
        if re.search(r"Failure \[.*?\]", out):
            raise AdbError("Installation Failure\n%s" % out)

        return out

    def path_app(self, package: str) -> str:
        """
        command 'adb shell pm path'
        :param package: package name
        :return: package path
        """
        try:
            stdout = self.shell(['pm', 'path', package])
        except AdbError:
            stdout = ''
        if 'package:' not in stdout:
            raise AdbError('package not found, stdout:[%s]' % stdout)
        return stdout.split('package:')[1].strip()

    def list_app(self, third_only=False) -> list:
        """
        command 'adb shell pm list packages'
        :return: list of packages
        """
        cmd = ["pm", "list", "packages"]
        if third_only:
            cmd.append("-3")
        output = self.shell(cmd)
        packages = output.splitlines()
        # remove all empty string; "package:xxx" -> "xxx"
        packages = [p.split(":")[1] for p in packages if p]
        return packages

    def app_is_running(self, name: str) -> bool:
        """判断应用是否在运行"""
        shell = "ps | grep -w {}".format(name)

    def start_app(self, package: str, activity: str = None):
        """
        commands 'adb shell monkey'
        if activity is None then commands 'adb shell am start'
        :param package: package name
        :param activity: activity name
        :return: None
        """
        if activity:
            self.shell(['am', 'start', '-n', '%s/%s.%s' % (package, package, activity)])
        else:
            self.shell(['monkey', '-p', package, '-c', 'android.intent.category.LAUNCHER', '1'])
        logger.info('start app:{}', package)

    def get_foreground_app(self) -> str:
        """获取前台应用包名"""
        shell = "dumpsys window windows | grep mCurrentFocus | cut -d'/' -f1 | rev | cut -d' ' -f1 | rev"
        package = self.raw_shell(shell).rstrip()
        logger.info("'{}' is running in the foreground", package)
        return package

    def get_display_info(self):
        display_info = self.getPhysicalDisplayInfo()
        orientation = self.getDisplayOrientation()
        max_x, max_y = self.getMaxXY()
        display_info.update({
            "orientation": orientation,
            "rotation": orientation * 90,
            "max_x": max_x,
            "max_y": max_y,
        })
        return display_info


class ADB(_Device):
    pass
