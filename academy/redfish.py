"""An explicitly simulated, stateful Redfish subset and IPMI teaching console.

All actions affect Python objects, never a real machine. The training maintenance
endpoint is our own extension, not part of Redfish. Resource identifiers must be
discovered; the lab's fixed identifiers are not recommended for real equipment.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
import secrets
import shlex
from threading import RLock
import time

BASE = "/redfish/v1"


@dataclass
class Response:
    status: int
    body: dict
    headers: dict | None = None


class Simulator:
    def __init__(self, scenario="healthy"):
        if scenario not in ("healthy", "fan_failure", "host_down", "transient"):
            raise ValueError("Unknown training scenario")
        self.scenario = scenario
        self.lock = RLock()
        self.audit = []
        self.sessions = {}
        self.reset_count = 0
        self.transient_reads = 0
        self.tasks = {}
        self.pending = {}
        self.resources = self._resources()

    def _resources(self):
        def collection(name, ids):
            return {
                "@odata.id": f"{BASE}/{name}",
                "Members@odata.count": len(ids),
                "Members": [{"@odata.id": f"{BASE}/{name}/{i}"} for i in ids],
            }

        resources = {
            BASE + "/": {
                "@odata.id": BASE + "/",
                "RedfishVersion": "1.19.0",
                **{
                    k: {"@odata.id": f"{BASE}/{k}"}
                    for k in (
                        "Systems",
                        "Chassis",
                        "Managers",
                        "TaskService",
                        "EventService",
                        "UpdateService",
                        "SessionService",
                    )
                },
            },
            f"{BASE}/SessionService": {
                "Sessions": {"@odata.id": f"{BASE}/SessionService/Sessions"}
            },
            f"{BASE}/Systems": collection("Systems", ["node-01", "node-02"]),
            f"{BASE}/Chassis": collection("Chassis", ["chassis-1"]),
            f"{BASE}/Managers": collection("Managers", ["bmc-1"]),
            f"{BASE}/Managers/bmc-1": {
                "@odata.id": f"{BASE}/Managers/bmc-1",
                "ManagerType": "BMC",
                "Name": "Training management controller",
                "Status": {"State": "Enabled", "Health": "OK"},
                "LogServices": {"@odata.id": f"{BASE}/Managers/bmc-1/LogServices"},
            },
            f"{BASE}/Managers/bmc-1/LogServices": collection(
                "Managers/bmc-1/LogServices", ["EventLog"]
            ),
            f"{BASE}/Managers/bmc-1/LogServices/EventLog": {
                "Entries": {"@odata.id": f"{BASE}/Managers/bmc-1/LogServices/EventLog/Entries"}
            },
            f"{BASE}/Managers/bmc-1/LogServices/EventLog/Entries": {"Members": []},
            f"{BASE}/Chassis/chassis-1": {
                "Name": "Training chassis",
                "Status": {"Health": "OK"},
                "Thermal": {"@odata.id": f"{BASE}/Chassis/chassis-1/Thermal"},
                "Power": {"@odata.id": f"{BASE}/Chassis/chassis-1/Power"},
            },
            f"{BASE}/Chassis/chassis-1/Thermal": {
                "Temperatures": [
                    {"Name": "CPU inlet", "ReadingCelsius": 42, "Status": {"Health": "OK"}}
                ],
                "Fans": [
                    {
                        "MemberId": "fan-1",
                        "Name": "Fan 1",
                        "Reading": 6000,
                        "ReadingUnits": "RPM",
                        "Status": {"State": "Enabled", "Health": "OK"},
                    }
                ],
            },
            f"{BASE}/Chassis/chassis-1/Power": {"PowerControl": [{"PowerConsumedWatts": 240}]},
            f"{BASE}/TaskService": {"Tasks": {"@odata.id": f"{BASE}/TaskService/Tasks"}},
            f"{BASE}/TaskService/Tasks": {"Members": []},
            f"{BASE}/EventService": {
                "ServiceEnabled": True,
                "Subscriptions": {"@odata.id": f"{BASE}/EventService/Subscriptions"},
            },
            f"{BASE}/EventService/Subscriptions": {"Members": []},
            f"{BASE}/UpdateService": {
                "FirmwareInventory": {"@odata.id": f"{BASE}/UpdateService/FirmwareInventory"},
                "Actions": {
                    "#UpdateService.SimpleUpdate": {
                        "target": f"{BASE}/UpdateService/Actions/UpdateService.SimpleUpdate"
                    }
                },
            },
            f"{BASE}/UpdateService/FirmwareInventory": {
                "Members": [{"@odata.id": f"{BASE}/UpdateService/FirmwareInventory/bmc"}]
            },
            f"{BASE}/UpdateService/FirmwareInventory/bmc": {
                "Version": "demo-1.0",
                "Updateable": True,
            },
        }
        for i, ram in (("node-01", 32), ("node-02", 64)):
            resources[f"{BASE}/Systems/{i}"] = {
                "@odata.id": f"{BASE}/Systems/{i}",
                "Id": i,
                "Name": "Training server " + i,
                "PowerState": "On",
                "Status": {"State": "Enabled", "Health": "OK"},
                "Boot": {
                    "BootSourceOverrideTarget": "None",
                    "BootSourceOverrideEnabled": "Disabled",
                    "BootSourceOverrideTarget@Redfish.AllowableValues": ["None", "Pxe", "Hdd"],
                },
                "MemorySummary": {"TotalSystemMemoryGiB": ram},
                "ProcessorSummary": {"Count": 2 if i == "node-01" else 4},
                "Bios": {"@odata.id": f"{BASE}/Systems/{i}/Bios"},
                "Actions": {
                    "#ComputerSystem.Reset": {
                        "target": f"{BASE}/Systems/{i}/Actions/ComputerSystem.Reset",
                        "ResetType@Redfish.AllowableValues": [
                            "On",
                            "GracefulShutdown",
                            "GracefulRestart",
                        ],
                    }
                },
            }
            resources[f"{BASE}/Systems/{i}/Bios"] = {
                "Attributes": {"BootMode": "Legacy"},
                "@Redfish.Settings": {
                    "SettingsObject": {"@odata.id": f"{BASE}/Systems/{i}/Bios/Settings"}
                },
            }
            resources[f"{BASE}/Systems/{i}/Bios/Settings"] = {"Attributes": {}}
        resources[f"{BASE}/Systems/node-02"]["Actions"]["#ComputerSystem.Reset"][
            "ResetType@Redfish.AllowableValues"
        ] = ["On", "GracefulShutdown"]
        if self.scenario == "fan_failure":
            resources[f"{BASE}/Chassis/chassis-1/Thermal"]["Fans"][0].update(
                Reading=0, Status={"State": "Enabled", "Health": "Critical"}
            )
            resources[f"{BASE}/Chassis/chassis-1/Thermal"]["Temperatures"][0].update(
                ReadingCelsius=85, Status={"Health": "Critical"}
            )
            resources[f"{BASE}/Chassis/chassis-1"]["Status"]["Health"] = "Critical"
            resources[f"{BASE}/Managers/bmc-1/LogServices/EventLog/Entries"]["Members"] = [
                {
                    "Id": "1",
                    "MessageId": "Training.FanStalled",
                    "Message": "Fan 1 stopped; inspect cooling.",
                    "Severity": "Critical",
                    "Created": "2026-01-01T12:00:00Z",
                }
            ]
        else:
            resources[f"{BASE}/Managers/bmc-1/LogServices/EventLog/Entries"]["Members"] = [
                {
                    "Id": "1",
                    "MessageId": "Training.Healthy",
                    "Message": "Training scenario initialized.",
                    "Severity": "OK",
                    "Created": "2026-01-01T12:00:00Z",
                }
            ]
        if self.scenario == "host_down":
            resources[f"{BASE}/Systems/node-01"]["PowerState"] = "Off"
        return resources

    def login(self, role="viewer"):
        if role not in ("viewer", "operator"):
            raise ValueError("Choose viewer or operator")
        token = secrets.token_urlsafe(24)
        self.sessions[token] = (role, time.monotonic() + 1800)
        return token

    def request(self, method, path, body=None, token=None, confirmed=False):
        with self.lock:
            return self._request(method.upper(), path, body or {}, token, confirmed)

    def _request(self, method, path, body, token, confirmed):
        path = path.rstrip("/") if path != BASE + "/" else path
        if path == BASE:
            path += "/"
        if not path.startswith(BASE) and path != "/training/v1/maintenance/replace-fan":
            return Response(
                400, {"error": "Use a local resource path; external URLs are not allowed."}
            )
        public = path in (BASE + "/", BASE + "/SessionService/Sessions")
        session = self.sessions.get(token)
        if not public and (not session or session[1] < time.monotonic()):
            return Response(401, {"error": "Create a training session first."})
        if method not in ("GET", "POST", "PATCH", "DELETE"):
            return Response(405, {"error": "Unsupported method."})
        if method == "DELETE" and token and path == BASE + "/SessionService/Sessions/" + token[:8]:
            self.sessions.pop(token, None)
            self.audit.append({"method": method, "path": path, "role": session[0]})
            return Response(204, {})
        if method != "GET" and not public:
            if session[0] != "operator":
                return Response(403, {"error": "The viewer role cannot modify resources."})
            if not confirmed:
                return Response(
                    409, {"error": "Preview the action and confirm the simulated change."}
                )
        self.audit.append(
            {"method": method, "path": path, "role": session[0] if session else "public"}
        )
        if method == "POST" and path == BASE + "/SessionService/Sessions":
            role = body.get("UserName")
            if body.get("Password") != "training-only" or role not in ("viewer", "operator"):
                return Response(
                    401, {"error": "Demo accounts: viewer or operator; password: training-only."}
                )
            token = self.login(role)
            return Response(
                201,
                {"Id": token[:8], "Name": "Training session"},
                {"X-Auth-Token": token, "Location": BASE + "/SessionService/Sessions/" + token[:8]},
            )
        if method == "GET":
            if (
                self.scenario == "transient"
                and path == BASE + "/Systems/node-02"
                and self.transient_reads == 0
            ):
                self.transient_reads += 1
                return Response(
                    503, {"error": "Injected temporary unavailability; retry this read."}
                )
            if path in self.tasks:
                task = self.tasks[path]
                task["PercentComplete"] = min(100, task["PercentComplete"] + 50)
                if task["PercentComplete"] == 100:
                    task["TaskState"] = "Completed"
                    self.resources[BASE + "/UpdateService/FirmwareInventory/bmc"]["Version"] = (
                        "demo-2.0"
                    )
                return Response(200, deepcopy(task))
            return (
                Response(200, deepcopy(self.resources[path]))
                if path in self.resources
                else Response(404, {"error": "Resource absent. Follow advertised links."})
            )
        if method == "POST" and path.endswith("/Actions/ComputerSystem.Reset"):
            system_path = path.split("/Actions/")[0]
            system = self.resources.get(system_path)
            if not system:
                return Response(404, {"error": "Unknown system."})
            kind = body.get("ResetType")
            allowed = system["Actions"]["#ComputerSystem.Reset"][
                "ResetType@Redfish.AllowableValues"
            ]
            if kind not in allowed:
                return Response(400, {"error": "ResetType unsupported; inspect AllowableValues."})
            system["PowerState"] = "Off" if kind == "GracefulShutdown" else "On"
            if kind == "GracefulRestart":
                self.reset_count += 1
                if system["Boot"]["BootSourceOverrideEnabled"] == "Once":
                    system["LastBootTarget"] = system["Boot"]["BootSourceOverrideTarget"]
                    system["Boot"]["BootSourceOverrideEnabled"] = "Disabled"
                if system_path in self.pending:
                    self.resources[system_path + "/Bios"]["Attributes"].update(
                        self.pending.pop(system_path)
                    )
                    self.resources[system_path + "/Bios/Settings"]["Attributes"] = {}
            return Response(200, {"PowerState": system["PowerState"], "Simulation": True})
        if method == "PATCH" and path in (BASE + "/Systems/node-01", BASE + "/Systems/node-02"):
            boot = body.get("Boot", {})
            if not isinstance(boot, dict) or set(boot) != {
                "BootSourceOverrideTarget",
                "BootSourceOverrideEnabled",
            }:
                return Response(
                    400, {"error": "Provide the supported target and enablement fields in Boot."}
                )
            if boot["BootSourceOverrideTarget"] not in ("None", "Pxe", "Hdd") or boot[
                "BootSourceOverrideEnabled"
            ] not in ("Once", "Disabled"):
                return Response(400, {"error": "Unsupported teaching boot override."})
            self.resources[path]["Boot"].update(boot)
            return Response(
                200, {"Boot": deepcopy(self.resources[path]["Boot"]), "Simulation": True}
            )
        if method == "PATCH" and path.endswith("/Bios/Settings") and path in self.resources:
            attrs = body.get("Attributes", {})
            if (
                not isinstance(attrs, dict)
                or set(attrs) != {"BootMode"}
                or attrs["BootMode"] not in ("Uefi", "Legacy")
            ):
                return Response(
                    400, {"error": "This training model supports BootMode=Uefi or Legacy."}
                )
            self.pending[path.split("/Bios/")[0]] = attrs.copy()
            self.resources[path]["Attributes"] = attrs.copy()
            return Response(200, {"Attributes": attrs, "PendingReset": True})
        if method == "POST" and path == BASE + "/UpdateService/Actions/UpdateService.SimpleUpdate":
            if body.get("ImageURI") != "training://firmware/demo-2.0":
                return Response(
                    400,
                    {
                        "error": "Only the built-in training firmware is accepted; no downloads occur."
                    },
                )
            task_path = BASE + "/TaskService/Tasks/" + str(len(self.tasks) + 1)
            self.tasks[task_path] = {
                "@odata.id": task_path,
                "TaskState": "Running",
                "PercentComplete": 0,
            }
            self.resources[BASE + "/TaskService/Tasks"]["Members"].append({"@odata.id": task_path})
            return Response(202, deepcopy(self.tasks[task_path]), {"Location": task_path})
        if method == "POST" and path == BASE + "/EventService/Subscriptions":
            if body.get("Destination") != "training://event-inbox":
                return Response(
                    400,
                    {"error": "Use training://event-inbox; this simulator never makes callbacks."},
                )
            self.resources[path]["Members"].append({"Destination": body["Destination"]})
            return Response(201, {"Destination": body["Destination"], "Simulation": True})
        if method == "POST" and path == "/training/v1/maintenance/replace-fan":
            if body.get("MemberId") != "fan-1":
                return Response(404, {"error": "Unknown fan."})
            thermal = self.resources[BASE + "/Chassis/chassis-1/Thermal"]
            thermal["Fans"][0].update(Reading=6000, Status={"State": "Enabled", "Health": "OK"})
            thermal["Temperatures"][0].update(ReadingCelsius=42, Status={"Health": "OK"})
            self.resources[BASE + "/Chassis/chassis-1"]["Status"]["Health"] = "OK"
            return Response(
                200,
                {
                    "Simulation": True,
                    "Replaced": "fan-1",
                    "Note": "A technician performs this physical operation on real equipment.",
                },
            )
        return Response(405, {"error": "That operation is not implemented in the training subset."})

    def ipmi(self, command, token=None, confirmed=False):
        if len(command) > 200:
            raise ValueError("Command too long")
        words = shlex.split(command)
        if words and words[0] == "ipmitool":
            words = words[1:]
        key = " ".join(words)
        reads = {
            "sensor": BASE + "/Chassis/chassis-1/Thermal",
            "sdr": BASE + "/Chassis/chassis-1/Thermal",
            "sel list": BASE + "/Managers/bmc-1/LogServices/EventLog/Entries",
            "fru print": BASE + "/Systems/node-01",
            "chassis power status": BASE + "/Systems/node-01",
        }
        if key in reads:
            result = self.request("GET", reads[key], token=token)
            return {
                "status": result.status,
                "output": result.body,
                "note": "Educational command mapping, not a native IPMI implementation.",
            }
        if key in ("chassis power on", "chassis power off", "chassis power cycle"):
            reset = {"on": "On", "off": "GracefulShutdown", "cycle": "GracefulRestart"}[words[-1]]
            result = self.request(
                "POST",
                BASE + "/Systems/node-01/Actions/ComputerSystem.Reset",
                {"ResetType": reset},
                token,
                confirmed,
            )
            return {
                "status": result.status,
                "output": result.body,
                "note": "Simplified teaching mapping: native IPMI power-off/cycle semantics differ from graceful Redfish actions.",
            }
        raise ValueError(
            "Supported teaching commands: sensor, sdr, sel list, fru print, chassis power status/on/off/cycle. No shell commands run."
        )


def inventory(sim, token, retries=2, workers=2):
    if (
        not isinstance(retries, int)
        or not 0 <= retries <= 3
        or not isinstance(workers, int)
        or not 1 <= workers <= 4
    ):
        raise ValueError("Use 0–3 bounded retries and 1–4 workers.")
    root = sim.request("GET", BASE + "/", token=token).body
    systems = sim.request("GET", root["Systems"]["@odata.id"], token=token)
    if systems.status != 200:
        raise ValueError("Cannot discover systems.")

    def read(member):
        for _ in range(retries + 1):
            response = sim.request("GET", member["@odata.id"], token=token)
            if response.status == 200:
                return response.body
            if response.status not in (429, 503):
                break
        return {"error": response.status, "path": member["@odata.id"]}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(read, systems.body["Members"]))


def exposition(sim):
    thermal = sim.resources[BASE + "/Chassis/chassis-1/Thermal"]
    temp = thermal["Temperatures"][0]["ReadingCelsius"]
    rpm = thermal["Fans"][0]["Reading"]
    return f'# TYPE training_inlet_temperature_celsius gauge\ntraining_inlet_temperature_celsius{{chassis="chassis-1"}} {temp}\n# TYPE training_fan_speed_rpm gauge\ntraining_fan_speed_rpm{{fan="fan-1"}} {rpm}\n'
