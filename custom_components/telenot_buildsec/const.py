"""Constants for Telenot BuildSec."""

DOMAIN = "telenot_buildsec"
VERSION = "1.2.0"

CONF_FREISCHALTCODE = "freischaltcode"
CONF_BEDIENTEILCODE = "bedienteilcode"
CONF_ADDRESS = "bedienteil_adresse"

CONF_ARM_AWAY_KEYS = "arm_away_keys"
CONF_ARM_HOME_KEYS = "arm_home_keys"
CONF_DISARM_KEYS = "disarm_keys"
CONF_AWAY_LED = "away_led"
CONF_HOME_LED = "home_led"
CONF_CODE_REQUIRED = "code_required"

# Verified on a complex 400H: function key -> panel asks "Code:" -> code -> OK (B).
# A leading Esc (S) cancels any input still pending from an earlier attempt.
DEFAULT_ARM_AWAY_KEYS = "SE{code}B"  # E = Taste "Extern scharf"
DEFAULT_ARM_HOME_KEYS = "SI{code}B"  # I = Taste "Intern scharf"
DEFAULT_DISARM_KEYS = "SA{code}B"  # A = Taste "Unscharf"
# Sequences from older versions that are known not to work -> replaced by the defaults.
LEGACY_KEY_SEQUENCES = {
    "{code}E": DEFAULT_ARM_AWAY_KEYS, "E{code}": DEFAULT_ARM_AWAY_KEYS,
    "{code}I": DEFAULT_ARM_HOME_KEYS, "I{code}": DEFAULT_ARM_HOME_KEYS,
    "{code}A": DEFAULT_DISARM_KEYS, "A{code}": DEFAULT_DISARM_KEYS,
}
LED_AUTO = "auto"
LED_NONE = "none"
LED_CHOICES = [LED_AUTO, LED_NONE, "1", "2", "3", "4"]

SERVICE_SEND_KEYS = "send_keys"
ATTR_KEYS = "keys"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"


def signal_update(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_update"
