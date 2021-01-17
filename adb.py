# -*- coding: utf-8 -*-
import os
import re
import sys
import time
import random
import socket
import platform
import warnings
import subprocess
import json
import threading
from typing import Union

import cv2
import numpy as np
from loguru import logger

THISPATH = os.path.dirname(os.path.realpath(__file__))
STATICPATH = os.path.join(THISPATH, "static")
DEFAULT_ADB_PATH = {
    "Windows": os.path.join(STATICPATH, "adb", "windows", "adb.exe"),
    "Darwin": os.path.join(STATICPATH, "adb", "mac", "adb"),
    "Linux": os.path.join(STATICPATH, "adb", "linux", "adb"),
    "Linux-x86_64": os.path.join(STATICPATH, "adb", "linux", "adb"),
    "Linux-armv7l": os.path.join(STATICPATH, "adb", "linux_arm", "adb"),
}


def split_cmd(cmds):
    """
    Split the commands to the list for subprocess
    Args:
        cmds: command(s)
    Returns:
        array commands
    """
    # cmds = shlex.split(cmds)  # disable auto removing \ on windows
    return cmds.split() if isinstance(cmds, str) else list(cmds)


def get_std_encoding(stream):
    return getattr(stream, "encoding", None) or sys.getfilesystemencoding()


class AdbError(Exception):
    """
        This is AdbError BaseError
        When ADB have something wrong
    """

    def __init__(self, stdout, stderr):
        self.stdout = stdout
        self.stderr = stderr

    def __str__(self):
        return "stdout[%s] stderr[%s]" % (self.stdout, self.stderr)


class ADB(object):
    """adb object class"""
    status_device = 'device'
    status_offline = 'offline'
    SHELL_ENCODING = 'utf-8'

    def __init__(self, device_id=None, adb_path=None, host='127.0.0.1', port=5037):
        self.device_id = device_id
        self.adb_path = adb_path or self.builtin_adb_path()
        self._set_cmd_options(host, port)
        self._sdk_version = 0  # sdk版本
        self._forward_local_using = self.get_forwards()  # 已经使用的端口
        self.connect()
        self._abi_version = self.abi_version()
        self._sdk_version = self.sdk_version()
        self._display_info = []  # 需要通过minicap模块获取

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
            logger.debug('adb -s {} {}'.format(self.device_id, " ".join(cmds)))
        else:
            cmd_options = self.cmd_options
            logger.debug('adb %s' % " ".join(cmds))

        cmds = cmd_options + cmds
        proc = subprocess.Popen(
            cmds,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW
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

    def cmd(self, cmds: Union[list, str], devices: bool = True, ensure_unicode: bool = True, timeout: int = None):
        """
        用cmds创建adb命令,并且返回stdout

        Args:
            cmds: 需要运行的参数,可以是list,str
            devices: 如果为True,则需要指定device-id,命令中会传入-s
            ensure_unicode: 是否解码stdout,stderr
            timeout: 设置命令超时时间

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
                logger.error("Command {cmd} time out after {timeout} seconds: stdout['{stdout}'], stderr['{stderr}']".
                             format(cmd=proc.args, timeout=timeout,
                                    stdout=stdout, stderr=stderr))
                raise
        else:
            stdout, stderr = proc.communicate()

        if ensure_unicode:
            stdout = stdout.decode(get_std_encoding(stdout))
            stderr = stderr.decode(get_std_encoding(stderr))

        if proc.returncode > 0:
            # adb error
            raise logger.error("adb connection{stdout} {stderr}".format(stdout=stdout, stderr=stderr))
        return stdout

    def devices(self, state: bool = None):
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

    def connect(self):
        """
        command adb connect

        Returns:
             None
        """
        if self.device_id and ':' in self.device_id:
            connect_result = self.cmd("connect %s" % self.device_id)
            logger.info(connect_result)

    def disconnect(self):
        """
        command adb disconnect

        Returns:
             None
        """
        if ':' in self.device_id:
            self.cmd("disconnect %s" % self.device_id)

    def start_shell(self, cmds: Union[list, str]) -> start_cmd:
        cmds = ['shell'] + split_cmd(cmds)
        return self.start_cmd(cmds)

    def raw_shell(self, cmds: Union[list, str], ensure_unicode: bool = True):
        cmds = ['shell'] + split_cmd(cmds)
        stdout = self.cmd(cmds, ensure_unicode=False)
        if not ensure_unicode:
            return stdout
        try:
            return stdout.decode(self.SHELL_ENCODING)
        except UnicodeDecodeError:
            logger.error('shell output decode {} fail. repr={}'.format(self.SHELL_ENCODING, repr(stdout)))
            return str(repr(stdout))

    def shell(self, cmd: Union[list, str]):

        if self.sdk_version < 25:
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
                raise logger.error("stdout={},stderr={}".format(err.stdout, err.stderr))
            else:
                return out

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
            self._forward_local_using.append({'local': local, 'remote': remote})
            logger.debug('forward {} {}'.format(local, remote))
        else:
            logger.debug('{} {} has been forward'.format(self._forward_local_using[index]['local'],
                                                         self._forward_local_using[index]['remote']))

    def get_forwards(self) -> list:
        """
        command adb forward --list

        :return:
            返回一个包含占用信息的列表,每个包含键值local和remote
        """
        l = []
        out = self.cmd(['forward', '--list'])
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
            sock.bind(('127.0.0.1', port))
            result = True
            # logger.debug('port:{} can use'.format(port))
        except:
            logger.debug('port:{} is in use'.format(port))
        sock.close()
        if not result:
            return self.get_available_forward_local()
        return port

    def set_forward(self, remote: str):
        """
        通过get_available_forward_local获取可用端口,并与remote绑定

        Args:
            remote: 要与local绑定的设备端口 localabstract:{remote}"

        :return:
            None
        """
        localport = self.get_available_forward_local()
        self.forward('tcp:%s' % localport, remote)

    def _local_in_forwards(self, local: str = None, remote: str = None) -> bool:
        """
        检查local是否已经启用

        :return:
            bool, if True return index in _forward_local_using
        """
        l = self.get_forwards()
        for i in range(len(l)):
            if local:
                if l[i]['local'] == local:
                    return True, i
            if remote:
                if l[i]['remote'] == remote:
                    return True, i
        return False, -1

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
        local_using, index = local and self._local_in_forwards(local) or (False, -1)
        # 删除在_forward_local_using里的记录
        if local_using:
            del self._forward_local_using[index]

    def push(self, local, remote) -> None:
        """
        运行adb push
        :param local:
            需要发送的文件路径
        :param remote:
            发送到设备上的路径
        :return:
            None
        """
        self.cmd(["push", local, remote], ensure_unicode=False)

    def pull(self, remote, local) -> None:
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

    def abi_version(self):
        """ get abi (application binary interface) """
        abi = self.raw_shell(['getprop', 'ro.product.cpu.abi'])
        logger.info('device {} abi is {}'.format(self.device_id, abi).rstrip('\r\n'))
        return abi

    def sdk_version(self):
        """ get sdk version """
        sdk = self.raw_shell(['getprop', 'ro.build.version.sdk'])
        logger.info('device {} sdk is {}'.format(self.device_id, sdk).rstrip('\r\n'))
        return sdk

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


class _Minicap(ADB):
    HOME = '/data/local/tmp'
    MNC_HOME = '/data/local/tmp/minicap'
    MNC_SO_HOME = '/data/local/tmp/minicap.so'
    MNC_CMD = 'LD_LIBRARY_PATH=/data/local/tmp /data/local/tmp/minicap'
    MNC_CAP_PATH = 'temp.png'
    MNC_PORT = 0

    def _push_target_mnc(self):
        """ push specific minicap """
        mnc_path = "./android/{}/bin/minicap".format(self._abi_version)
        # logger.debug('target minicap path: ' + mnc_path)

        # push and grant
        self.start_cmd(['push', mnc_path, self.MNC_HOME])
        self.start_shell(['chmod', '777', self.MNC_HOME])
        logger.debug('minicap installed in {}'.format(self.MNC_HOME))

    def _push_target_mnc_so(self):
        """ push specific minicap.so (they should work together) """
        mnc_so_path = './android/{}/lib/android-{}/minicap.so'.format(self._abi_version, self._sdk_version)
        # logger.debug('target minicap.so url: ' + mnc_so_path)

        # push and grant
        self.start_cmd(['push', mnc_so_path, self.MNC_SO_HOME])
        self.start_shell(['chmod', '777', self.MNC_SO_HOME])
        logger.debug('minicap.so installed in {}'.format(self.MNC_SO_HOME))

    def _is_mnc_install(self):
        """
        check if minicap and minicap.so installed

        :return:
            None
        """
        return self.check_file(self.HOME, 'minicap') and self.check_file(self.HOME, 'minicap.so')

    def set_minicap_port(self):
        """
        command foward to minicap
        :return:
        """
        self.set_forward('localabstract:minicap')
        index = self._local_in_forwards(remote='localabstract:minicap')
        self.MNC_PORT = int(re.compile(r'tcp:(\d+)').findall(self._forward_local_using[index[1]]['local'])[0])

    def get_display_info(self):
        """
        command adb shell minicap -i
        :return:
            display information
        """
        display_info = self.raw_shell([self.MNC_CMD, '-i'])
        match = re.compile(r'({.*})', re.DOTALL).search(display_info)
        display_info = match.group(0) if match else display_info
        display_info = json.loads(display_info)
        display_info["orientation"] = display_info["rotation"] / 90
        # adb获取分辨率
        wm_size = self.raw_shell(['wm', 'size'])
        wm_size = re.findall(r'Physical size: (\d+)x(\d+)\r', wm_size)
        if len(wm_size) > 0:
            display_info['physical_width'] = display_info['width']
            display_info['physical_height'] = display_info['height']
            display_info['width'] = int(wm_size[0][0])
            display_info['height'] = int(wm_size[0][1])
        # adb方式获取DPI
        wm_dpi = self.raw_shell(['wm', 'density'])
        wm_dpi = re.findall(r'Physical density: (\d+)\r', wm_dpi)
        if len(wm_dpi) > 0:
            display_info['dpi'] = int(wm_dpi[0])
        logger.debug('display_info {}', display_info)
        self._display_info = display_info
        return display_info

    def start_mnc_server(self):
        """
        command adb shell {self.MNC_CMD} -P 1920x1080@1920x1080/0 开启minicap服务
        :return:
            None
        """
        display_info = self.get_display_info()
        self.start_shell([self.MNC_CMD, '-P', '%dx%d@%dx%d/%d' % (display_info['width'], display_info['height'],
                                                                  display_info['width'], display_info['height'],
                                                                  display_info['rotation'])])
        time.sleep(1)

    def screencap(self):
        """
        通过socket读取minicap的图片数据,并且通过cv2生成图片
        :return:
            cv2.img
        """
        readBannerBytes = 0
        bannerLength = 2
        readFrameBytes = 0
        frameBodyLengthRemaining = 0
        frameBody = ''
        banner = {
            'version': 0,
            'length': 0,
            'pid': 0,
            'realWidth': 0,
            'realHeight': 0,
            'virtualWidth': 0,
            'virtualHeight': 0,
            'orientation': 0,
            'quirks': 0
        }
        client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_socket.connect(('localhost', self.MNC_PORT))
        self.start_shell([self.MNC_CMD, "-n 'minicap' -s"])
        width, height = self._display_info['width'], self._display_info['height']
        while True:
            chunk = client_socket.recv(36000)  # 调大可用加快速度,但是24000以上基本就没有差距了
            if len(chunk) == 0:
                continue

            cursor = 0
            while cursor < len(chunk):
                if (readBannerBytes < bannerLength):
                    if readBannerBytes == 0:
                        banner['version'] = int(hex(chunk[cursor]), 16)
                    elif readBannerBytes == 1:
                        banner['length'] = bannerLength = int(hex(chunk[cursor]), 16)
                    elif readBannerBytes >= 2 and readBannerBytes <= 5:
                        banner['pid'] = int(hex(chunk[cursor]), 16)
                    elif readBannerBytes == 23:
                        banner['quirks'] = int(hex(chunk[cursor]), 16)

                    cursor += 1
                    readBannerBytes += 1


                elif readFrameBytes < 4:
                    frameBodyLengthRemaining += (int(hex(chunk[cursor]), 16) << (readFrameBytes * 8))
                    cursor += 1
                    readFrameBytes += 1

                else:
                    # if this chunk has data of next image
                    if len(chunk) - cursor >= frameBodyLengthRemaining:
                        frameBody = frameBody + chunk[cursor:(cursor + frameBodyLengthRemaining)]
                        if hex(frameBody[0]) != '0xff' or hex(frameBody[1]) != '0xd8':
                            exit()
                        img = np.array(bytearray(frameBody))
                        img = cv2.imdecode(img, 1)
                        img = cv2.resize(img, (width, height))
                        cv2.imwrite(self.MNC_CAP_PATH, img)
                        client_socket.close()
                        return img
                    else:
                        # else this chunk is still for the current image
                        frameBody = bytes(list(frameBody) + list(chunk[cursor:len(chunk)]))
                        frameBodyLengthRemaining -= (len(chunk) - cursor)
                        readFrameBytes += len(chunk) - cursor
                        cursor = len(chunk)


class Device(_Minicap):
    pass


def connect(device_id=None, adb_path=None, host='127.0.0.1', port=5037):
    return Device(device_id, adb_path, host, port)
