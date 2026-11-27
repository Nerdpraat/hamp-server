"""Constants for the Energy Hub integration."""
DOMAIN = "energy_hub"

CONF_URL = "url"
CONF_TOKEN = "token"            # the site key from the server's auth.yaml

# Options
CONF_AUTO_SYNC = "auto_sync"    # follow the HA energy dashboard automatically
CONF_EXCLUDED = "excluded"      # ids of discovered instances the user removed
CONF_DEVICES = "devices"        # instances the user added: [{id, device, name, sensors}]
CONF_THROTTLE = "throttle"      # seconds between updates per sensor

DEFAULT_THROTTLE = 5
MIN_THROTTLE = 1
MAX_THROTTLE = 300
