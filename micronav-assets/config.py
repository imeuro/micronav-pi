#!/usr/bin/env python3
"""
Configurazione per MicroNav Raspberry Pi
Contiene tutte le impostazioni per MQTT, display, WiFi e sistema
"""

import os
from dotenv import load_dotenv
from typing import Dict, Any

# Carica il file .env
load_dotenv()

BASE_PATH = os.getenv('BASE_PATH')

# Configurazione Sistema
SYSTEM_CONFIG = {
    'app_name': 'MicroNav',
    'version': '0.2.0',
    'base_path': os.getenv('BASE_PATH'),
    'logs_path': f'{BASE_PATH}/logs/',
    'config_path': f'{BASE_PATH}/config/',
    'assets_path': f'{BASE_PATH}/micronav-assets/',
    'pid_file': f'{BASE_PATH}/micronav.pid',
    'service_name': 'micronav.service'
}

# Configurazione WiFi
WIFI_CONFIG = {
    'home_network': {
        'ssid': os.getenv('WIFI_HOME_SSID'),
        'password': os.getenv('WIFI_HOME_PASSWORD'),
        'priority': 10,
        'security': 'wpa-psk'
    },
    'mobile_network': {
        'ssid': os.getenv('WIFI_MOBILE_SSID'),
        'password': os.getenv('WIFI_MOBILE_PASSWORD'),
        'priority': 5,
        'security': 'wpa-psk'
    },
    'monitor_interval': 30,  # secondi
    'connection_timeout': 10  # secondi
}

# Configurazione MQTT
MQTT_CONFIG = {
    'broker_host': os.getenv('MQTT_BROKER_HOST'),
    'broker_port': int(os.getenv('MQTT_BROKER_PORT')),
    'broker_websocket_port': int(os.getenv('MQTT_BROKER_WS_PORT')),
    'username': os.getenv('MQTT_USERNAME'),
    'password': os.getenv('MQTT_PASSWORD'),
    'device_id': os.getenv('DEVICE_ID'),
    'keepalive': 60,
    'reconnect_attempts': 5,
    'reconnect_delay': 5
}

# Configurazione Topics MQTT
def get_mqtt_topics(device_id: str = None) -> Dict[str, Any]:
    """Restituisce i topic MQTT per un device_id specifico"""
    if device_id is None:
        device_id = MQTT_CONFIG['device_id']
    
    base_topic = f"micronav/device/{device_id}"
    
    return {
        'device_id': device_id,
        'base_topic': base_topic,
        'subscribe': {
            'route_data': f"{base_topic}/route/data",
            'route_step': f"{base_topic}/route/step", 
            'commands': f"{base_topic}/commands",
            'gps_position': f"{base_topic}/gps/position"
        },
        'publish': {
            'status': f"{base_topic}/status",
            'display_current': f"{base_topic}/display/current",
            'system_info': f"{base_topic}/system/info"
        }
    }

# Topic MQTT per il device corrente
MQTT_TOPICS = get_mqtt_topics()

# Configurazione Display TFT ST7789V3 1.47" Waveshare
# da specifiche è 320x172, ma il alla fine la corretta risoluzione del display è 320x240. non so dire perchè.
DISPLAY_CONFIG = {
    'width': 320,           # Risoluzione orizzontale (H)RGB
    'height': 240,          # Risoluzione verticale (V)
    'rotate': 2,            # 0, 1, 2, 3 per rotazioni
    'spi_port': 0,          # SPI port 0
    'spi_device': 0,        # SPI device 0 (CE0)
    'spi_speed': 40000000,  # 40MHz (velocità SPI)
    'spi_mode': 0,          # Modalità SPI
    'bgr': True,            # BGR=True per ST7789V3 (formato colore)
    'invert': False,        # Non invertire colori
    'h_offset': 0,          # Offset orizzontale
    'v_offset': 0           # Offset verticale
}

# Configurazione GPIO per Display ST7789V3 1.47" Waveshare
# Connessioni secondo documentazione Waveshare:
# CS -> GPIO 8 (Pin 24) - Chip Select (CE0)
# DC -> GPIO 25 (Pin 22) - Data/Command
# RST -> GPIO 27 (Pin 13) - Reset
# BL -> GPIO 18 (Pin 12) - Backlight
# DIN -> GPIO 10 (Pin 19) - MOSI (automatico)
# CLK -> GPIO 11 (Pin 23) - SCLK (automatico)
GPIO_CONFIG = {
    'TFT_CS': 8,    # GPIO 8 (Pin 24) - Chip Select
    'TFT_DC': 25,   # GPIO 25 (Pin 22) - Data/Command
    'TFT_RST': 27,  # GPIO 27 (Pin 13) - Reset
    'TFT_BL': 18    # GPIO 18 (Pin 12) - Backlight
}

# Configurazione Colori per Display
COLORS = {
    'black': (0, 0, 0),
    'white': (255, 255, 255),
    'red': (255, 0, 0),
    'green': (0, 255, 0),
    'blue': (0, 0, 255),
    'yellow': (255, 255, 0),
    'orange': (255, 165, 0),
    'gray': (128, 128, 128),
    'dark_gray': (64, 64, 64),
    'light_gray': (192, 192, 192)
}

# Configurazione Font per Display
FONT_CONFIG = {
    'lcd_fonts': {
        'paths': [
            f'{BASE_PATH}/micronav-assets/font/LcdSolid-VPzB.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        ],
        'sizes': {
            'small': 12,
            'medium': 16,
            'large': 24
        }
    },
    'system_fonts': {
        'paths': [
            f'{BASE_PATH}/micronav-assets/font/Figtree-Black.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        ],
        'sizes': {
            'small': 14,
            'medium': 18,
            'large': 32
        }
    }
}


BOOT_IMAGE_CONFIG = {
    # w320 x h240
    'path': f'{BASE_PATH}/micronav-assets/_boot.jpg',
    'size': (320, 240),
    'time': 3
}
DIRECTIONS_ICONS_CONFIG = {
    'path': f'{BASE_PATH}/micronav-assets/directions-icons/src/png/light',
    'size': 128
}


# Configurazione Logging
LOGGING_CONFIG = {
    'level': 'INFO',
    'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    'file': f'{BASE_PATH}/micronav-assets/logs/micronav.log',
    'max_size': 10 * 1024 * 1024,  # 10MB
    'backup_count': 5
}





def get_config() -> Dict[str, Any]:
    """Restituisce la configurazione completa"""
    return {
        'mqtt': MQTT_CONFIG,
        'display': DISPLAY_CONFIG,
        'gpio': GPIO_CONFIG,
        'wifi': WIFI_CONFIG,
        'topics': MQTT_TOPICS,
        'colors': COLORS,
        'fonts': FONT_CONFIG,
        'boot_image': BOOT_IMAGE_CONFIG,
        'directions_icons': DIRECTIONS_ICONS_CONFIG,
        'logging': LOGGING_CONFIG,
        'system': SYSTEM_CONFIG
    }

def get_mqtt_config() -> Dict[str, Any]:
    """Restituisce la configurazione MQTT"""
    return MQTT_CONFIG.copy()

def get_display_config() -> Dict[str, Any]:
    """Restituisce la configurazione display"""
    return DISPLAY_CONFIG.copy()

def get_gpio_config() -> Dict[str, Any]:
    """Restituisce la configurazione GPIO"""
    return GPIO_CONFIG.copy()

def get_wifi_config() -> Dict[str, Any]:
    """Restituisce la configurazione WiFi"""
    return WIFI_CONFIG.copy()

def get_topics_config(device_id: str = None) -> Dict[str, Any]:
    """Restituisce la configurazione topics MQTT"""
    return get_mqtt_topics(device_id)

def get_colors_config() -> Dict[str, tuple]:
    """Restituisce la configurazione colori"""
    return COLORS.copy()

def get_font_config() -> Dict[str, Any]:
    """Restituisce la configurazione font"""
    return FONT_CONFIG.copy()

def get_boot_image_config() -> Dict[str, Any]:
    """Restituisce la configurazione font"""
    return BOOT_IMAGE_CONFIG.copy()

def get_directions_icons_config() -> Dict[str, Any]:
    """Restituisce la configurazione icone direzioni"""
    return DIRECTIONS_ICONS_CONFIG.copy()

def get_logging_config() -> Dict[str, Any]:
    """Restituisce la configurazione logging"""
    return LOGGING_CONFIG.copy()

def validate_config() -> bool:
    """Valida la configurazione"""
    try:
        # Verifica configurazione MQTT
        if not MQTT_CONFIG.get('broker_host'):
            print("❌ broker_host non configurato")
            return False
        
        if not MQTT_CONFIG.get('device_id'):
            print("❌ device_id non configurato")
            return False
        
        # Verifica configurazione display
        if DISPLAY_CONFIG.get('width') <= 0 or DISPLAY_CONFIG.get('height') <= 0:
            print("❌ Dimensioni display non valide")
            return False
        
        # Verifica configurazione GPIO
        for pin_name, pin_num in GPIO_CONFIG.items():
            if not (0 <= pin_num <= 27):
                print(f"❌ Pin {pin_name} (GPIO {pin_num}) non valido")
                return False
        
        print("✅ Configurazione valida")
        return True
        
    except Exception as e:
        print(f"❌ Errore validazione configurazione: {e}")
        return False

def print_config_summary():
    """Stampa un riepilogo della configurazione"""
    print("=" * 60)
    print("📋 CONFIGURAZIONE MICRONAV RASPBERRY PI")
    print("=" * 60)
    
    print(f"\n🔗 MQTT:")
    print(f"  Broker: {MQTT_CONFIG['broker_host']}:{MQTT_CONFIG['broker_port']}")
    print(f"  Device ID: {MQTT_CONFIG['device_id']}")
    print(f"  Username: {MQTT_CONFIG['username']}")
    
    print(f"\n🖥️  Display:")
    print(f"  Risoluzione: {DISPLAY_CONFIG['width']}x{DISPLAY_CONFIG['height']}")
    print(f"  Rotazione: {DISPLAY_CONFIG['rotate']}°")
    print(f"  SPI: {DISPLAY_CONFIG['spi_port']}.{DISPLAY_CONFIG['spi_device']}")
    
    print(f"\n🔌 GPIO:")
    for name, pin in GPIO_CONFIG.items():
        print(f"  {name}: GPIO {pin}")
    
    print(f"\n📡 WiFi:")
    print(f"  Rete Casa: {WIFI_CONFIG['home_network']['ssid']}")
    print(f"  Rete Mobile: {WIFI_CONFIG['mobile_network']['ssid']}")
    
    print(f"\n📁 Sistema:")
    print(f"  App: {SYSTEM_CONFIG['app_name']} v{SYSTEM_CONFIG['version']}")
    print(f"  Path: {SYSTEM_CONFIG['base_path']}")
    
    print("=" * 60)

if __name__ == "__main__":
    print_config_summary()
    
    print("\n🔍 Validazione configurazione...")
    if validate_config():
        print("✅ Configurazione valida!")
    else:
        print("❌ Configurazione non valida!")
        print("\nModifica i valori in config.py per correggere gli errori.")

