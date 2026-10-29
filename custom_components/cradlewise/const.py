DOMAIN = "cradlewise"

CRADLE_ID = "29976d6e-4bbf-433c-bdf6-0ed6606e3fc5"
# Crib's LOCAL MQTT broker (the crib itself, nursery VLAN). The AWS IoT
# endpoint (a2bby18smixe1f-ats.iot.us-east-1.amazonaws.com) no longer accepts
# this integration's identity for shadow subscriptions since the 2026-10-03
# policy change — see coordinator.py CLIENT_ID note.
HOST = "192.168.14.231"
PORT = 8883

SHADOW_GET     = f"$aws/things/{CRADLE_ID}/shadow/get"
SHADOW_GET_ACC = f"$aws/things/{CRADLE_ID}/shadow/get/accepted"
SHADOW_UPDATE  = f"$aws/things/{CRADLE_ID}/shadow/update"
SHADOW_UPD_ACC = f"$aws/things/{CRADLE_ID}/shadow/update/accepted"
SHADOW_UPD_REJ = f"$aws/things/{CRADLE_ID}/shadow/update/rejected"
SHADOW_DELTA   = f"$aws/things/{CRADLE_ID}/shadow/update/delta"

SLEEP_STATE_MAP = {
    0: "away",
    1: "awake",
    2: "stirring",
    3: "stirring",
    4: "sleep",
    5: "awake",
    6: "stirring",
}
