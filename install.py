"""
Скрипт устанавливает Sonic Tuna в качестве systemd сервиса.
В процессе установки будет создан пользователь, указанный параметром --user (по-умолчанию sonic-tuna)
"""
import argparse
import getpass
import os
import pwd
import random
import shutil


def user_exists(username):
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False


def exec_command(command):
    print(f'[#] {command}')
    res = os.system(command)
    if res != 0:
        raise RuntimeError(f'Failed to execute...')


def main(args: argparse.Namespace):
    if user_exists(args.user):
        print(f"User {args.user} already exists. Maybe Sonic-Tuna is already installed")
        print('Exiting...')
        return
    nologin_shell = shutil.which("nologin") or "/usr/sbin/nologin"
    exec_command(f'useradd -s {nologin_shell} {args.user}')
    exec_command(f'mkdir /home/{args.user}')
    exec_command(f'chown {args.user} /home/{args.user}')
    for file in ['tuna-ip.py', 'tuna-server.py', 'requirements.txt', 'src/util/batch_generator.py',
                 'src/util/dct_encoder.py', 'src/util/encoder.py', 'src/browser.py', 'src/camera_bridge.js',
                 'src/input_cameras.js', 'src/tun.py', 'src/ws_server.py']:
        exec_command(f'install -D -o {args.user} -g {args.user} {file} /home/{args.user}/{file}')

    exec_command(f'sudo -u {args.user} python3 -m venv /home/{args.user}/.venv')
    exec_command(f'sudo -u {args.user} /home/{args.user}/.venv/bin/pip install --upgrade pip')
    exec_command(
        f'sudo -u {args.user} /home/{args.user}/.venv/bin/pip install --upgrade -r /home/{args.user}/requirements.txt')
    exec_command(f'sudo -u {args.user} /home/{args.user}/.venv/bin/playwright install chromium')

    exec_command(f'mkdir -p /etc/sonic-tuna')

    print('Creating config template "/etc/sonic-tuna/config.yaml"')
    with open('/etc/sonic-tuna/config.yaml', 'w') as f:
        f.write(f"""
log_level: INFO # logging level

device: tuna # interface (see `ip a`)
address: {args.ip} # ip address and netmask of this device. For example, 10.0.0.1/24
mtu: 1400 # interface MTU. Don't change if not sure what you do.
route_table: 8042 # id of route table will be created

# gateway: 10.0.0.x # if specified, this ip address will be used as default gateway.
# That means, all outgoing traffic will be routed by sonic-tuna interface.
# Specified gateway address has to be in same subnet as this device address.
# Make sure that there is sonic-tuna started as nat at device with ip address, specified as gateway for this.
# Can't be used with 'nat' option

# nat: true # if specified, this device will be configured as nat gateway.
# That means, all incoming traffic will be redirected to default route interface.
# You can also write `nat: <interface name>`. For example: `nat: eth0`
# Can't be used with 'gateway' option
        
call_url: {args.url}# Yandex Telemost join url (https://telemost.yandex.ru/j/<conference id>)
port: 8042 # websocket port, used for internal process communication
username: user{random.randint(1, 100)} # nickname for Yandex Telemost conference
        
# frame_scale: 1 # Scale factor. Image will be upscaled before sending via Yandex Telemost.
# frame_size: 256x256 # Virtual web camera resolution. Must be divided by 16.
# fps: 20 # Virtual web camera fps.
# disable_reed_solomon: true # Disable usign Reed Solomon error correction codes. Uncomment if your PC is potato :D
""")
    exec_command(f'chown {args.user}:{args.user} /etc/sonic-tuna')
    exec_command(f'chown {args.user}:{args.user} /etc/sonic-tuna/config.yaml')

    print('Creating systemd service "/etc/systemd/system/sonic-tuna.service"')
    with open('/etc/systemd/system/sonic-tuna.service', 'w') as f:
        f.write(f"""
[Unit]
Description=Sonic Tuna service - virtual personal network via WebRTC conference.
After=network.target

[Service]
PermissionsStartOnly=true
Type=simple
Group={args.user}
User={args.user}
WorkingDirectory=/home/{args.user}
ExecStartPre=/home/{args.user}/.venv/bin/python tuna-ip.py up --config /etc/sonic-tuna/config.yaml
ExecStart=/home/{args.user}/.venv/bin/python tuna-server.py --config /etc/sonic-tuna/config.yaml
ExecStopPost=/home/{args.user}/.venv/bin/python tuna-ip.py down --config /etc/sonic-tuna/config.yaml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
        """)
    exec_command("systemctl daemon-reload")

    print('Installation completed. Finish configuring Sonic Tuna in file "/etc/sonic-tuna/config.yaml"')
    print('Then you can start Tuna by command "sudo systemctl start sonic-tuna"')


if __name__ == "__main__":
    if getpass.getuser() != 'root':
        print("This script requires root privileges")
        exit(1)
    parser = argparse.ArgumentParser(prog="install", description="Sonic Tuna installer")

    parser.add_argument("-u", "--user", type=str, default='sonic-tuna', help="System user will be created")
    parser.add_argument("--url", type=str, default='', help="Yandex Telemost conference link")
    parser.add_argument("--ip", type=str, default='10.0.0.1', help="Network interface ip address")

    main(parser.parse_args())
