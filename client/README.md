# Allocator Client

Command-line tool (and Python library) for requesting USB devices from the allocator
manager. You define **groups** of device names, open a **session** to allocate a group on a
node, then `usbip attach` the devices locally. All orchestration goes through the manager over
HTTP.

---

## Install

The easy way — run the setup script (creates a venv, installs the shared contract + client,
verifies dependencies):

```bash
cd client
./setup.sh                 # add --dev for test extras, --venv DIR for a custom location
source .venv/bin/activate
```

Manual install (the client depends on the sibling `contract/` package — install it first):

```bash
cd client
pip install -e ../contract -e .
```

`usbip` is needed only to **attach** devices a session hands out (Debian/Ubuntu:
`sudo apt install usbip linux-tools-generic`). You can browse/manage without it.

---

## Configure

| Key | Default | Purpose |
|---|---|---|
| `url` | `http://localhost:8000` | Manager base URL |
| `client_id` | your hostname | Your identity — **owns your sessions** |

There is **no API key**. Your identity is your hostname (override the `client_id` key).
Reuse the same identity to manage sessions you created — sessions are owned by it, and only
the owner can release/freeze them.

Config is a small JSON file at `~/.config/allocator/config.json` (defaults live in code; the
file holds only values you set). Manage it with `allocator config`:

```bash
allocator config set url http://allocator.local   # persists to ~/.config/allocator/config.json
allocator config get url
allocator config show                              # effective config + file path
allocator config unset url                         # fall back to the default
```

In the library, override per instance (`AllocatorClient(manager_url=...)`) or use the client's
config: `client.config.get("url")` / `client.config.set("url", "http://allocator.local")`
(persists immediately).

---

## Commands

### Browse inventory
```bash
allocator node list                       # nodes, status, frozen flag
allocator node show <node>                # full details (name or id)
allocator device list [--node N] [--status FREE|ALLOCATED|ERROR] [--class WIFI|HID|...]
allocator device show <node> <name>       # device names are unique PER NODE
```

### Name a device (optional)
Devices get an auto name like `wifi_0`, `hid_1`. Give one a custom name that **follows the
physical device across nodes**:
```bash
allocator device name <node> <current_name> <new_name>
```
If the new name is already used on that node, you'll be prompted:
**[1] make the other device generic · [2] choose a different name · [3] abort**.

### Groups (templates of device names)
```bash
allocator group create mygroup --devices wifi_0,hid_1     # comma-separated names
allocator group list                                      # shows Available + Active Sessions
allocator group show mygroup
allocator group delete mygroup [--yes]
```
A group is just a list of names; it's matched to actual devices on a node when you start a
session. `Available` = some online, unfrozen node currently has all those devices free.

### Sessions (allocate a group)
```bash
allocator session create --group mygroup [--node <node>]   # -g / -n
allocator session list [--status ACTIVE|PENDING|RELEASED|FAILED]
allocator session show <session_id>
allocator session release <session_id>                     # frees + unbinds the devices
```
- The manager picks **one node** that has every group device free (fewest-extra-devices wins),
  or use `--node` to pin a specific node.
- If no node can satisfy the group right now, the session is **PENDING** (queued) and starts
  automatically when a node frees up. Nothing is bound while queued.
- A successful session is **ACTIVE** and prints an attach command per device.

### Freeze a node (reserve it)
While you hold an active session you can freeze its node so no one else can be allocated it —
it **stays frozen even after you release**, until someone unfreezes it:
```bash
allocator session freeze <session_id>     # freeze the node this session runs on
allocator node unfreeze <node>            # anyone can unfreeze
```

---

## Attaching the devices (USB/IP)

`session create`/`show` print a ready-to-run command per device, e.g.:

```
Device          Node IP        Bus ID   Attach Command
mass_storage_0  10.0.0.5       1-10.1   usbip attach -r 10.0.0.5 -b 1-10.1
```

Run it locally (needs `usbip` + the `vhci-hcd` module):

```bash
sudo modprobe vhci-hcd
sudo usbip attach -r 10.0.0.5 -b 1-10.1   # the device now appears as local USB
# ... use the device ...
sudo usbip detach -p <port>               # 'usbip port' lists ports
allocator session release <session_id>    # then release on the manager
```

---

## Typical workflow

```bash
export ALLOCATOR_URL=http://manager:8000

allocator device list                                  # find device names per node
allocator group create lab --devices wifi_0,hid_0
allocator session create --group lab                   # -> ACTIVE + attach commands
sudo usbip attach -r <node_ip> -b <bus_id>             # for each device
# ... work ...
allocator session release <session_id>
```

---

## Library use

`AllocatorClient` (in `allocator_client/client.py`) is a **synchronous** client (built on
`httpx.Client`, no event loop) that returns typed models from `allocator_contract`:

```python
from allocator_client import AllocatorClient

with AllocatorClient("http://manager:8000") as c:   # identity defaults to hostname
    nodes = c.list_nodes()                          # -> NodeListResponse
    s = c.create_session("lab")                     # -> SessionResponse
    for d in s.devices:
        print(d.logical_name, d.usbip_attach_command)
    c.release_session(s.session_id)
```

`rename_device` raises `allocator_client.NameConflict` on a name collision (when `force=False`).

`AllocatorSession` allocates a group, runs `usbip attach` for each device, and on exit detaches
and releases — with rollback if an attach fails midway (needs root for the usbip ops):

```python
from allocator_client import AllocatorClient, AllocatorSession

with AllocatorClient("http://manager:8000") as client:
    with AllocatorSession(client, "lab") as session:
        # every device is already usbip-attached here
        for d in session.devices:
            print(d.logical_name, "-> local port", d.local_port)
        # ... use the devices ...
    # detach + release happen automatically on exit
```
