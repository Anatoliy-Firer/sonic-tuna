import argparse, os, json, subprocess


def get_interfaces():
    result = subprocess.run(['ip', '-j', 'addr'], capture_output=True, text=True)
    data = json.loads(result.stdout)

    interfaces = [iface['ifname'] for iface in data]
    return set(interfaces)


def main():
    parser = argparse.ArgumentParser(prog="tuna-util", description="Sonic Tuna interface utils")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    up_parser = subparsers.add_parser("up", help="Create and up interface")
    up_parser.add_argument("-a", "--address", type=str, help="Ipv4 address with subnet")
    up_parser.add_argument("-u", "--user", type=str, default='sonic-tuna', help="System user, owner of interface")
    up_parser.add_argument("-d", "--device", type=str, default="tuna", help="Interface name")
    up_parser.add_argument("-п", "--gateway", action="store_true",
                           help="If set, this interface will be configured as gateway")
    up_parser.add_argument("--mtu", type=int, default=1400, help="MTU size")

    down_parser = subparsers.add_parser("down", help="Turn off and remove interface")
    down_parser.add_argument("-d", "--device", type=str, default="tuna", help="Interface name")

    args = parser.parse_args()

    if args.command == "up":
        if not args.address:
            up_parser.print_help()
            return
        if args.device in get_interfaces():
            print(f"Interface {args.device} already exists")
            return
        if args.gateway:
            print("Not supported yet")

        os.system(f"sudo ip tuntap add dev {args.device} mode tun user {args.user}")
        os.system(f"sudo ip addr add {args.address} dev {args.device}")
        os.system(f"sudo ip link set dev {args.device} mtu {args.mtu}")
        os.system(f"sudo ip link set dev {args.device} up")
    elif args.command == "down":
        os.system(f"sudo ip link delete {args.device}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
