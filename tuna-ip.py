import argparse
import ipaddress
import json
import pwd
import shutil
import subprocess

AUTO_NAT_DEVICE = "__auto_nat_device__"


def run_command(command, check=True):
    print(f'[#] {" ".join(command)}')
    result = subprocess.run(command, capture_output=True, text=True)
    if check and result.returncode != 0:
        error_text = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"Command failed: {' '.join(command)}: {error_text}")
    return result


def run_command_with_input(command, input_text, check=True):
    print(f'[#] {" ".join(command)}')
    result = subprocess.run(command, input=input_text, capture_output=True, text=True)
    if check and result.returncode != 0:
        error_text = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"Command failed: {' '.join(command)}: {error_text}")
    return result


def get_default_route_device():
    result = run_command(["ip", "-j", "route", "show", "default"])
    routes = json.loads(result.stdout)

    for route in routes:
        device = route.get("dev")
        if device:
            return device

    raise RuntimeError("Could not detect default network interface from routing table")


def user_exists(username):
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False


def create_system_user(username):
    nologin_shell = shutil.which("nologin") or "/usr/sbin/nologin"
    return run_command(["sudo", "useradd", "-M", "-s", nologin_shell, username], check=False)


def get_nat_table_name(route_table):
    return f"tuna_nat_{route_table}"


def validate_args(args):
    if getattr(args, "gateway", None) and getattr(args, "nat", None):
        raise RuntimeError("Options --gateway and --nat cannot be used together")


def validate_gateway_address(interface_address, gateway_address):
    network = ipaddress.ip_interface(interface_address).network
    gateway = ipaddress.ip_address(gateway_address)
    if gateway not in network:
        raise RuntimeError(
            f"Gateway {gateway_address} is not in subnet {network} from address {interface_address}"
        )


def normalize_interface_address(address):
    if "/" not in address:
        return f"{address}/24"
    return address


def get_user_rule_pref(route_table):
    if route_table <= 0:
        raise RuntimeError("--route-table must be greater than 0 when used with --gateway")
    return route_table


def configure_gateway(device, username, route_table, gateway):
    user_uid = pwd.getpwnam(username).pw_uid
    user_rule_pref = get_user_rule_pref(route_table)

    run_command(["sudo", "ip", "route", "replace", "default", "via", gateway, "dev", device, "table", str(route_table)])
    run_command([
        "sudo", "ip", "rule", "del", "pref", str(user_rule_pref),
        "not", "uidrange", f"{user_uid}-{user_uid}", "lookup", str(route_table),
    ], check=False, )
    run_command([
        "sudo", "ip", "rule", "add", "pref",
        str(user_rule_pref), "not", "uidrange", f"{user_uid}-{user_uid}",
        "lookup", str(route_table),
    ])


def cleanup_gateway(route_table, username=None):
    user_rule_pref = get_user_rule_pref(route_table)

    if username and user_exists(username):
        user_uid = pwd.getpwnam(username).pw_uid
        run_command([
            "sudo", "ip", "rule", "del", "pref", str(user_rule_pref),
            "not", "uidrange", f"{user_uid}-{user_uid}", "lookup", str(route_table),
        ], check=False, )
    run_command(["sudo", "ip", "route", "flush", "table", str(route_table)], check=False)


def configure_nat(device, nat_device, subnet, route_table):
    nat_table = get_nat_table_name(route_table)

    run_command(["sudo", "sysctl", "-w", "net.ipv4.ip_forward=1"])
    run_command(["sudo", "nft", "delete", "table", "ip", nat_table], check=False)
    ruleset = f"""table ip {nat_table} {{
    chain forward {{
        type filter hook forward priority filter; policy accept;
        iifname "{device}" oifname "{nat_device}" accept
        iifname "{nat_device}" oifname "{device}" ct state related,established accept
    }}

    chain postrouting {{
        type nat hook postrouting priority srcnat; policy accept;
        oifname "{nat_device}" ip saddr {subnet} masquerade
    }}
}}
"""
    run_command_with_input(["sudo", "nft", "-f", "-"], ruleset)


def cleanup_nat(route_table):
    run_command(["sudo", "nft", "delete", "table", "ip", get_nat_table_name(route_table)], check=False)


def cleanup_interface(device):
    run_command(["sudo", "ip", "link", "delete", device], check=False)


def cleanup_up_state(device, route_table, username):
    cleanup_gateway(route_table, username)
    cleanup_nat(route_table)
    cleanup_interface(device)


def main():
    parser = argparse.ArgumentParser(prog="tuna-ip", description="Sonic Tuna inet interface util")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    up_parser = subparsers.add_parser("up", help="Create and up interface")
    up_parser.add_argument("-a", "--address", type=str, help="Ipv4 address with subnet")
    up_parser.add_argument("-u", "--user", type=str, default="sonic-tuna", help="System user, owner of interface")
    up_parser.add_argument("-c", "--create-user", action="store_true", help="Create system user if not exists")
    up_parser.add_argument("-d", "--device", type=str, default="tuna", help="Interface name")
    up_parser.add_argument("-g", "--gateway", type=str, default=None, help="Gateway IP in the same subnet as --address")
    up_parser.add_argument(
        "--nat",
        nargs="?",
        const=AUTO_NAT_DEVICE,
        help="Configure NAT for traffic from tuna to this device, default is the current default-route interface",
    )
    up_parser.add_argument("--mtu", type=int, default=1400, help="MTU size")
    up_parser.add_argument("--route-table", type=int, default=8042,
                           help="Policy routing table used by gateway")

    down_parser = subparsers.add_parser("down", help="Turn off and remove interface")
    down_parser.add_argument("-d", "--device", type=str, default="tuna", help="Interface name")
    down_parser.add_argument("-u", "--user", type=str, default="sonic-tuna", help="System user, owner of interface")
    down_parser.add_argument("--route-table", type=int, default=8042, help="Policy routing table used by gateway")

    args = parser.parse_args()

    try:
        validate_args(args)
    except RuntimeError as error:
        print(error)
        return

    if args.command == "up":
        if not args.address:
            up_parser.print_help()
            return
        args.address = normalize_interface_address(args.address)
        try:
            interface_network = ipaddress.ip_interface(args.address)
        except ValueError:
            print(f"Invalid interface address: {args.address}")
            return
        if not user_exists(args.user):
            if args.create_user:
                create_result = create_system_user(args.user)
                if create_result.returncode != 0:
                    error_text = create_result.stderr.strip() or create_result.stdout.strip() or "unknown error"
                    print(f"Failed to create user {args.user}: {error_text}")
                    return
                print(f"Created user {args.user}")
            else:
                print(
                    f"User {args.user} does not exist. "
                    "Use --create-user to create it automatically without a home directory and with nologin shell."
                )
                return
        if args.gateway:
            try:
                validate_gateway_address(args.address, args.gateway)
            except ValueError:
                print(f"Invalid gateway address: {args.gateway}")
                return
            except RuntimeError as error:
                print(error)
                return
        if args.nat == AUTO_NAT_DEVICE:
            try:
                args.nat = get_default_route_device()
                print(f"Device {args.nat} used as default route device")
            except RuntimeError as error:
                print(error)
                return

        try:
            cleanup_up_state(args.device, args.route_table, args.user)
            run_command(["sudo", "ip", "tuntap", "add", "dev", args.device, "mode", "tun", "user", args.user])
            run_command(["sudo", "ip", "addr", "add", args.address, "dev", args.device])
            run_command(["sudo", "ip", "link", "set", "dev", args.device, "mtu", str(args.mtu)])
            run_command(["sudo", "ip", "link", "set", "dev", args.device, "up"])
            if args.gateway:
                configure_gateway(args.device, args.user, args.route_table, args.gateway)
            elif args.nat:
                subnet = str(interface_network.network)
                configure_nat(args.device, args.nat, subnet, args.route_table)
        except RuntimeError as error:
            print(error)
            return

    elif args.command == "down":
        try:
            cleanup_up_state(args.device, args.route_table, args.user)
        except RuntimeError as error:
            print(error)
            return
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
