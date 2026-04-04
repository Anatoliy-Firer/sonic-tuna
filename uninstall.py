"""
Скрипт удаляет Sonic Tuna из системы.
В процессе установки будет удалён пользователь, указанный параметром --user (по-умолчанию sonic-tuna)
"""
import argparse
import getpass
import os
import pwd


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
    print(f'Sonic Tuna uninstalling')
    if not user_exists(args.user):
        print(f"User {args.user} does not exists")
        print('Exiting...')
        return
    decision = input(f'User {args.user} will be permanently deleted. Are you sure? [y/N] ')
    if decision.strip().lower() != 'y':
        print('Uninstalling aborted...')
        print('Exiting...')
        return

    exec_command('systemctl stop sonic-tuna')
    exec_command(f'userdel -r {args.user}')
    exec_command('rm -r /etc/sonic-tuna')
    exec_command('rm /etc/systemd/system/sonic-tuna.service')
    exec_command('systemctl daemon-reload')

    print('Sonic Tuna successfully uninstalled')


if __name__ == "__main__":
    if getpass.getuser() != 'root':
        print("This script requires root privileges")
        exit(1)
    parser = argparse.ArgumentParser(prog="install", description="Sonic Tuna installer")

    parser.add_argument("-u", "--user", type=str, default='sonic-tuna', help="System user will be created")

    main(parser.parse_args())
