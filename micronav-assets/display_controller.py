#!/usr/bin/env python3
"""
Display Controller per MicroNav Raspberry Pi
Gestisce il display TFT ST7789 1.47" per visualizzare istruzioni di navigazione
"""

import time
import logging
import threading
import importlib
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import RPi.GPIO as GPIO

try:
    from luma.core.interface.serial import spi
    from luma.core.render import canvas
    from luma.lcd.device import st7789
    from luma.core.interface.parallel import bitbang_6800
except ImportError:
    print("❌ Librerie display non trovate. Installa con: pip install luma.lcd luma.core")
    exit(1)

import config
from logging_config import get_logger

# Inizializza logging
logger = get_logger(__name__)

class MicroNavDisplayController:
    """Controller per display TFT ST7789 MicroNav"""
    
    def __init__(self):
        """Inizializza il controller display"""
        self.device = None
        self.is_initialized = False
        self.current_instruction = None
        self.current_route = None
        self.current_speedcam = None  # Dati speedcam corrente
        self.current_speedcam_distance = None  # Distanza speedcam corrente
        self.current_connection_status = {  # Stato connessioni (overlay)
            'wifi_connected': False,
            'mqtt_connected': False,
            'gps_connected': False,
            'gps_has_fix': False
        }
        self.display_thread = None
        self.running = False
        
        # Buffer per aggiornamenti parziali
        self.current_display_image = None
        
        # Configurazione
        self._load_config()

        
        # Font e dimensioni
        self.fonts_sm = {
            'small': None,
            'medium': None,
            'large': None,
            'icon': None
        }
        self.fonts_sys = {
            'small': None,
            'medium': None,
            'large': None,
            'icon': None
        }
        
        # Stato display
        self.display_state = {
            'brightness': self.config['brightness'],
            'orientation': 0,
            'current_screen': 'idle',
            'last_update': None
        }
        
        # Cache immagini
        self.icon_cache = {}
        
        # Lock per proteggere accessi concorrenti al display
        self.display_lock = threading.Lock()
        
        logger.debug("Display Controller MicroNav inizializzato")
    
    def _load_config(self):
        """Carica la configurazione dal modulo config"""
        self.config = config.get_display_config()
        self.gpio_config = config.get_gpio_config()
        self.colors = config.get_colors_config()
        self.font_config = config.get_font_config()
        self.boot_image_config = config.get_boot_image_config()
        self.directions_icons_config = config.get_directions_icons_config()
    
    def reload_config_and_fonts(self):
        """Ricarica la configurazione e i font con le nuove dimensioni"""
        try:
            logger.debug("🔄 Ricaricamento configurazione e font...")
            
            # Ricarica il modulo config
            importlib.reload(config)
            logger.debug("✅ Modulo config ricaricato")
            
            # Ricarica la configurazione
            self._load_config()
            logger.debug("✅ Configurazione ricaricata")
            
            # Ricarica i font con le nuove dimensioni
            self._load_fonts()
            logger.info("✅ Font ricaricati con nuove dimensioni")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Errore ricaricamento configurazione: {e}")
            return False
    
    def initialize_display(self) -> bool:
        """Inizializza il display TFT ST7789"""
        logger.debug("🔧 Inizializzazione display ST7789...")
        
        try:
            # Configura GPIO
            logger.debug("📌 Configurazione GPIO...")
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            
            # Configura pin display
            GPIO.setup(self.gpio_config['TFT_CS'], GPIO.OUT)
            GPIO.setup(self.gpio_config['TFT_DC'], GPIO.OUT)
            GPIO.setup(self.gpio_config['TFT_RST'], GPIO.OUT)
            GPIO.setup(self.gpio_config['TFT_BL'], GPIO.OUT)
            logger.debug("✅ GPIO configurato")
            
            # Abilita backlight PRIMA di tutto e mantienilo acceso
            self._ensure_backlight_on()
            logger.debug("💡 Backlight acceso e protetto")
            
            # Reset display
            logger.debug("🔄 Reset display...")
            GPIO.output(self.gpio_config['TFT_RST'], GPIO.LOW)
            time.sleep(0.1)
            GPIO.output(self.gpio_config['TFT_RST'], GPIO.HIGH)
            time.sleep(0.1)
            logger.debug("✅ Reset completato")
            
            # Verifica backlight dopo reset
            self._ensure_backlight_on()
            
            # Configura interfaccia SPI
            logger.debug("🔌 Configurazione SPI...")
            serial = spi(
                port=0,
                device=0,
                gpio_DC=self.gpio_config['TFT_DC'],
                gpio_RST=self.gpio_config['TFT_RST'],
                gpio_CS=self.gpio_config['TFT_CS']
            )
            logger.debug("✅ SPI configurato")
            
            # Verifica backlight dopo SPI
            self._ensure_backlight_on()
            
            # Crea dispositivo ST7789
            logger.debug("🖥️ Creazione dispositivo ST7789...")
            logger.debug(f"   Dimensioni: {self.config['width']}x{self.config['height']}")
            logger.debug(f"   Rotazione: {self.config['rotate']}")
            logger.debug(f"   BGR: {self.config.get('bgr', False)}")
            logger.debug(f"   Invert: {self.config.get('invert', False)}")
            
            self.device = st7789(
                serial,
                width=self.config['width'],
                height=self.config['height'],
                rotate=self.config['rotate'],
                bgr=self.config.get('bgr', False),
                invert=self.config.get('invert', False)
            )
            logger.debug("✅ Dispositivo ST7789 creato")
            
            # Verifica backlight dopo creazione dispositivo
            self._ensure_backlight_on()
            
            # Carica font
            logger.debug("🔤 Caricamento font...")
            self._load_fonts()
            
            # Boot display
            logger.info("🧪 Boot display...")
            self._boot_display()
            
            # Verifica backlight dopo test
            self._ensure_backlight_on()
            
            self.is_initialized = True
            logger.debug("✅ Display TFT ST7789 inizializzato con successo")
            return True
            
        except Exception as e:
            logger.error(f"❌ Errore inizializzazione display: {e}")
            logger.error(f"   Tipo errore: {type(e).__name__}")
            # Mantieni backlight acceso durante i tentativi alternativi
            self._ensure_backlight_on()
    
    def _load_fonts(self):
        """Carica i font per il display"""
        try:
            # Font LCD per fonts_sm
            lcd_fonts = self.font_config['lcd_fonts']
            lcd_paths = lcd_fonts['paths']
            lcd_sizes = lcd_fonts['sizes']
            
            # Font di sistema per fonts_sys
            system_fonts = self.font_config['system_fonts']
            system_paths = system_fonts['paths']
            system_sizes = system_fonts['sizes']
            
            # Carica fonts_sm (font LCD)
            fonts_sm_loaded = False
            for font_path in lcd_paths:
                try:
                    self.fonts_sm['small'] = ImageFont.truetype(font_path, lcd_sizes['small'])
                    self.fonts_sm['medium'] = ImageFont.truetype(font_path, lcd_sizes['medium'])
                    self.fonts_sm['large'] = ImageFont.truetype(font_path, lcd_sizes['large'])
                    logger.debug(f"Font LCD caricati da: {font_path}")
                    fonts_sm_loaded = True
                    break
                except Exception as e:
                    logger.warning(f"Impossibile caricare font LCD da {font_path}: {e}")
                    continue
            
            # Carica fonts_sys (font di sistema)
            fonts_sys_loaded = False
            for font_path in system_paths:
                try:
                    self.fonts_sys['small'] = ImageFont.truetype(font_path, system_sizes['small'])
                    self.fonts_sys['medium'] = ImageFont.truetype(font_path, system_sizes['medium'])
                    self.fonts_sys['large'] = ImageFont.truetype(font_path, system_sizes['large'])
                    logger.debug(f"Font di sistema caricati da: {font_path}")
                    fonts_sys_loaded = True
                    break
                except Exception as e:
                    logger.warning(f"Impossibile caricare font di sistema da {font_path}: {e}")
                    continue
            
            # Fallback per fonts_sm se non caricati
            if not fonts_sm_loaded:
                logger.warning("Font LCD non trovati, uso font predefinito per fonts_sm")
                self.fonts_sm['small'] = ImageFont.load_default()
                self.fonts_sm['medium'] = ImageFont.load_default()
                self.fonts_sm['large'] = ImageFont.load_default()
            
            # Fallback per fonts_sys se non caricati
            if not fonts_sys_loaded:
                logger.warning("Font di sistema non trovati, uso font predefinito per fonts_sys")
                self.fonts_sys['small'] = ImageFont.load_default()
                self.fonts_sys['medium'] = ImageFont.load_default()
                self.fonts_sys['large'] = ImageFont.load_default()
            
            logger.debug("✅ Caricamento font completato")
            
        except Exception as e:
            logger.error(f"Errore caricamento font: {e}")
            # Font di emergenza per entrambi i set
            self.fonts_sm = {
                'small': ImageFont.load_default(),
                'medium': ImageFont.load_default(),
                'large': ImageFont.load_default(),
            }

            self.fonts_sys = {
                'small': ImageFont.load_default(),
                'medium': ImageFont.load_default(),
                'large': ImageFont.load_default(),
            }
    
    def _boot_display(self):
        """Test del display con pattern colorato"""
        try:
            # Carica e mostra l'immagine di boot
            boot_image_path = self.boot_image_config['path']
            boot_image_time = self.boot_image_config['time']
            try:
                import os
                if os.path.exists(boot_image_path):
                    logger.debug(f"Caricamento immagine: {boot_image_path}")
                    
                    # Carica l'immagine con gestione robusta
                    with Image.open(boot_image_path) as boot_image:
                        logger.debug(f"Immagine caricata: {boot_image.mode} {boot_image.size}")
                        
                        # Mostra l'immagine direttamente sul display
                        self.device.display(boot_image)
                        logger.debug("✅ Immagine di boot mostrata correttamente")
                else:
                    logger.warning(f"File immagine di boot non trovato: {boot_image_path}")
                    # Fallback con canvas e testo
                    with canvas(self.device) as draw:
                        # Sfondo completamente bianco per test visibilità
                        draw.rectangle(
                            (0, 0, self.config['width'], self.config['height']),
                            fill=self.colors['white']
                        )
                        draw.text(
                            (self.config['width']//2 - 50, self.config['height']//2 - 20),
                            "MicroNav",
                            font=self.fonts_sm['large'],
                            fill=self.colors['black']
                        )
                
            except Exception as e:
                logger.error(f"Errore caricamento immagine di boot: {e}")
                # Fallback con canvas e testo
                with canvas(self.device) as draw:
                    # Sfondo completamente bianco per test visibilità
                    draw.rectangle(
                        (0, 0, self.config['width'], self.config['height']),
                        fill=self.colors['white']
                    )
                    draw.text(
                        (self.config['width']//2 - 50, self.config['height']//2 - 20),
                        "MicroNav",
                        font=self.fonts_sys['large'],
                        fill=self.colors['black']
                    )
            
            time.sleep(boot_image_time)  # tempo per vedere il boot screen
            logger.debug("Test display completato")
            
        except Exception as e:
            logger.error(f"Errore test display: {e}")
    
    def clear_display(self):
        """Pulisce il display"""
        if not self.is_initialized:
            logger.warning("Display non inizializzato, impossibile pulire")
            return
        
        try:
            # Verifica che GPIO sia configurato
            if not hasattr(GPIO, '_mode'):
                logger.debug("GPIO non configurato, riconfigurazione...")
                GPIO.setmode(GPIO.BCM)
                GPIO.setwarnings(False)
            
            # Verifica che i pin siano configurati come OUTPUT
            try:
                # Prova a usare un pin per vedere se è configurato
                GPIO.output(self.gpio_config['TFT_BL'], GPIO.HIGH)
            except RuntimeError as e:
                if "not been set up as an OUTPUT" in str(e):
                    logger.debug("Pin GPIO non configurati, riconfigurazione...")
                    # Riconfigura tutti i pin
                    GPIO.setup(self.gpio_config['TFT_CS'], GPIO.OUT)
                    GPIO.setup(self.gpio_config['TFT_DC'], GPIO.OUT)
                    GPIO.setup(self.gpio_config['TFT_RST'], GPIO.OUT)
                    GPIO.setup(self.gpio_config['TFT_BL'], GPIO.OUT)
                    logger.debug("✅ Pin GPIO riconfigurati")
                else:
                    raise e
            
            with canvas(self.device) as draw:
                draw.rectangle(
                    (0, 0, self.config['width'], self.config['height']),
                    fill=self.colors['black']
                )
            
            # Non resettare il buffer quando si pulisce lo schermo
            # self.current_display_image = None
            
        except Exception as e:
            logger.error(f"Errore pulizia display: {e}")
            # Se c'è un errore, prova a riconfigurare GPIO
            try:
                GPIO.setmode(GPIO.BCM)
                GPIO.setwarnings(False)
                GPIO.setup(self.gpio_config['TFT_CS'], GPIO.OUT)
                GPIO.setup(self.gpio_config['TFT_DC'], GPIO.OUT)
                GPIO.setup(self.gpio_config['TFT_RST'], GPIO.OUT)
                GPIO.setup(self.gpio_config['TFT_BL'], GPIO.OUT)
                logger.debug("GPIO completamente riconfigurato per clear_display")
            except Exception as gpio_error:
                logger.error(f"Errore riconfigurazione GPIO: {gpio_error}")
            
    def reset_display(self):
        """Reset completo del display in caso di problemi gravi"""
        try:
            logger.warning("🔄 Reset completo del display")
            
            # Pulisci il display
            self.clear_display()
            time.sleep(0.5)
            
            # Mostra schermata di reset
            with canvas(self.device) as draw:
                # Sfondo rosso per indicare reset
                draw.rectangle(
                    (0, 0, self.config['width'], self.config['height']),
                    fill=self.colors['red']
                )
                
                # Testo reset
                draw.text(
                    (10, 50),
                    "RESET",
                    font=self.fonts_sm['large'],
                    fill=self.colors['white']
                )
                
                draw.text(
                    (10, 100),
                    "Display",
                    font=self.fonts_sm['medium'],
                    fill=self.colors['white']
                )
            
            time.sleep(2)
            
            # Torna alla schermata idle
            self.show_idle_screen()
            
            logger.info("✅ Reset display completato")
            
        except Exception as e:
            logger.error(f"❌ Errore durante reset display: {e}")
            # Ultimo tentativo: solo pulizia
            try:
                self.clear_display()
            except:
                pass
    
    def _draw_idle_content(self, draw):
        """Disegna il contenuto della schermata idle"""
        try:
            import os
            logo_path = '/home/micronav/micronav-pi/micronav-assets/micronav.png'
            
            # Sfondo nero
            draw.rectangle(
                (0, 0, self.config['width'], self.config['height']),
                fill=self.colors['black']
            )

            if os.path.exists(logo_path):
                logger.debug(f"Caricamento immagine: {logo_path}")
                
                # Carica l'immagine
                with Image.open(logo_path) as logo_image:
                    draw._image.paste(logo_image, (0, 0))
            else:
                 
                # Logo/titolo
                draw.text(
                    (self.config['width'] // 2 - 80, 90),
                    "MicroNav",
                    font=self.fonts_sys['large'],
                    fill=self.colors['white']
                )
                
            
            # Status
            status_text = "attesa percorso..."
            # Calcola larghezza testo usando textbbox per compatibilità
            bbox = draw.textbbox((0, 0), status_text, font=self.fonts_sys['small'])
            text_width = bbox[2] - bbox[0]
            text_x = (self.config['width'] - text_width) // 2
            
            draw.text(
                (text_x, 140),
                status_text,
                font=self.fonts_sys['small'],
                fill=self.colors['gray']
            )
            
        except Exception as e:
            logger.error(f"Errore disegno contenuto idle: {e}")

    def show_idle_screen(self):
        """Mostra schermata di attesa"""
        if not self.is_initialized:
            logger.warning("Display non inizializzato, impossibile mostrare schermata idle")
            return
        
        try:
            with self.display_lock:
                logger.debug("Mostrando schermata idle")
                
                # Aggiorna lo stato PRIMA di disegnare per evitare conflitti
                self.display_state['current_screen'] = 'idle'
                self.display_state['last_update'] = datetime.now()
                
                with canvas(self.device) as draw:
                    self._draw_idle_content(draw)
                    
                    # Disegna overlay speedcam se presente e permesso (non cambia current_screen)
                    if (self.current_speedcam and self.current_speedcam_distance is not None and 
                        self._should_show_speedcam_overlay()):
                        self._draw_speedcam_alert_content(draw, self.current_speedcam, self.current_speedcam_distance)
                    
                    # Disegna sempre gli indicatori di connessione (overlay)
                    status = self.current_connection_status
                    self._draw_wifi_indicator(draw, status.get('wifi_connected', False))
                    self._draw_mqtt_indicator(draw, status.get('mqtt_connected', False))
                    self._draw_gps_indicator(draw, status.get('gps_connected', False), status.get('gps_has_fix', False))
                
                # Salva l'immagine corrente per aggiornamenti parziali
                # Per la schermata idle non è critico, ma manteniamo coerenza
                self._save_current_display()
                
                logger.debug("Schermata idle visualizzata correttamente")
                
        except Exception as e:
            logger.error(f"Errore schermata idle: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")



### Schermate di navigazione

    def _draw_navigation_content(self, draw, instruction_data: Dict[str, Any] = None, safe_mode: bool = False):
        """Disegna il contenuto della schermata di navigazione"""
        try:
            # Usa i dati correnti se non forniti
            if instruction_data is None:
                instruction_data = self.current_instruction or {}
            
            instruction = instruction_data.get('instruction', '')
            distance = instruction_data.get('distance', 0)
            duration = instruction_data.get('duration', 0)
            maneuver = instruction_data.get('maneuver', {})
            icon = instruction_data.get('icon', '')
            
            # Sfondo
            draw.rectangle(
                (0, 0, self.config['width'], self.config['height']),
                fill=self.colors['black']
            )
            
            # Icona manovra (se disponibile)
            if maneuver:
                # Costruisce e logga il path dell'icona PNG
                icon_path = self._get_icon_path(instruction_data)
                logger.debug(f"Path icona PNG: {icon_path}")
                
                # Carica e visualizza l'icona PNG
                self._draw_maneuver_icon(draw, icon_path, 180, 65)
            
            # Istruzione principale
            self._draw_wrapped_text(
                draw,
                instruction,
                (10, 70),
                (self.config['width'] / 2) - 10,
                self.fonts_sys['medium'],
                self.colors['white']
            )
            
            # Distanza
            if distance > 0:
                distance_text = f"{distance}m"
                if distance >= 1000:
                    distance_text = f"{distance/1000:.1f}km"
                
                draw.text(
                    (10, 160),
                    distance_text,
                    font=self.fonts_sys['large'],
                    fill=self.colors['white']
                )

            # Salva l'immagine corrente per aggiornamenti parziali (solo se non in modalità safe)
            if not safe_mode:
                self._save_current_display()

            
        except Exception as e:
            logger.error(f"Errore disegno contenuto navigazione: {e}")

    def show_route_overview(self, route_data: Dict[str, Any]):
        """Mostra panoramica del percorso"""
        if not self.is_initialized:
            logger.error("Display non inizializzato per mostrare panoramica percorso")
            return
        
        with self.display_lock:
            try:
                
                origin = route_data.get('origin', '')
                destination = route_data.get('destination', '')
                logger.debug(f"Mostrando panoramica: origine='{origin[:50]}...', destinazione='{destination[:50]}...'")
                
                # Aggiorna lo stato PRIMA di disegnare per evitare conflitti
                self.current_route = route_data
                self.display_state['current_screen'] = 'route_overview'
                self.display_state['last_update'] = datetime.now()
                
                with canvas(self.device) as draw:
                    self._draw_route_overview_content(draw, route_data)
                    
                    # Disegna sempre gli indicatori di connessione (overlay)
                    status = self.current_connection_status
                    self._draw_wifi_indicator(draw, status.get('wifi_connected', False))
                    self._draw_mqtt_indicator(draw, status.get('mqtt_connected', False))
                    self._draw_gps_indicator(draw, status.get('gps_connected', False), status.get('gps_has_fix', False))
                
                # Salva l'immagine corrente per aggiornamenti parziali (DOPO il disegno)
                # Questo deve essere fatto immediatamente per evitare che update_connections_status
                # trovi current_display_image = None e ridisegni tutto
                self._save_current_display()
                
                logger.info(f"Panoramica percorso visualizzata: {origin} → {destination}")
                
            except Exception as e:
                logger.error(f"Errore visualizzazione panoramica: {e}")
    
    def _draw_route_overview_content(self, draw, route_data: Dict[str, Any] = None, safe_mode: bool = False):
        """Disegna il contenuto della schermata panoramica percorso"""
        try:
            # Usa i dati correnti se non forniti
            if route_data is None:
                route_data = self.current_route or {}
            
            origin = route_data.get('origin', '')
            destination = route_data.get('destination', '')
            total_distance = route_data.get('totalDistance', 0)
            total_duration = route_data.get('totalDuration', 0)
            steps = route_data.get('steps', [])
            
            # (RI)Carica font se necessario
            if not self.fonts_sys['large'] or not self.fonts_sys['medium'] or not self.fonts_sys['small']:
                self._load_fonts()
            
            # Sfondo
            draw.rectangle(
                (0, 0, self.config['width'], self.config['height']),
                fill=self.colors['black']
            )
            
            # Titolo
            draw.text(
                (10, 15),
                "Percorso",
                font=self.fonts_sm['large'],
                fill=self.colors['white']
            )
            
            # Origine
            draw.text(
                (10, 45),
                "Da:",
                font=self.fonts_sys['medium'],
                fill=self.colors['light_gray']
            )
            # Tronca l'origine se troppo lunga
            origin_short = origin[:50] + "..." if len(origin) > 50 else origin
            self._draw_wrapped_text(
                draw,
                origin_short,
                (10, 60),
                self.config['width'] - 20,
                self.fonts_sys['medium'],
                self.colors['white']
            )
            
            # Destinazione
            draw.text(
                (10, 100),
                "A:",
                font=self.fonts_sys['medium'],
                fill=self.colors['light_gray']
            )
            # Tronca la destinazione se troppo lunga
            destination_short = destination[:50] + "..." if len(destination) > 50 else destination
            self._draw_wrapped_text(
                draw,
                destination_short,
                (10, 115),
                self.config['width'] - 20,
                self.fonts_sys['medium'],
                self.colors['white']
            )
            
            # Distanza totale
            if total_distance > 0:
                distance_text = f"{total_distance}m"
                if total_distance >= 1000:
                    distance_text = f"{total_distance/1000:.1f}km"
                
                draw.text(
                    (10, 160),
                    f"{distance_text}",
                    font=self.fonts_sys['large'],
                    fill=self.colors['white']
                )
            
            # Durata totale
            if total_duration > 0:
                duration_text = f"{total_duration}s"
                if total_duration >= 60:
                    minutes = total_duration // 60
                    duration_text = f"{minutes}m"
                if total_duration >= 3600:
                    hours = total_duration // 3600
                    minutes = (total_duration % 3600) // 60
                    duration_text = f"{hours}h {minutes}m"
                
                draw.text(
                    (self.config['width'] - 10 - draw.textlength(f"{duration_text}", font=self.fonts_sys['large']), 160),
                    f"{duration_text}",
                    font=self.fonts_sys['large'],
                    fill=self.colors['light_gray']
                )
            
            # Salva l'immagine corrente per aggiornamenti parziali (solo se non in modalità safe)
            if not safe_mode:
                self._save_current_display()
            
        except Exception as e:
            logger.error(f"Errore disegno contenuto panoramica: {e}")

    def show_navigation_instruction(self, instruction_data: Dict[str, Any]):
        """Mostra istruzione di navigazione"""
        if not self.is_initialized:
            logger.error("Display non inizializzato per mostrare istruzione")
            return
        
        with self.display_lock:
            try:
                # Pulisci lo schermo prima di mostrare l'istruzione
                # logger.debug("Pulizia schermo prima di istruzione")
                # self.clear_display()
                # time.sleep(0.1)  # Piccola pausa per assicurarsi che la pulizia sia completata
                
                logger.debug(f"Inizio visualizzazione istruzione: {instruction_data.get('instruction', '')[:30]}...")
                
                # Aggiorna lo stato PRIMA di disegnare per evitare conflitti
                self.current_instruction = instruction_data
                self.display_state['current_screen'] = 'navigation'
                self.display_state['last_update'] = datetime.now()
                
                with canvas(self.device) as draw:
                    self._draw_navigation_content(draw, instruction_data)
                    
                    # Disegna overlay speedcam se presente (sempre visibile sopra navigation)
                    if self.current_speedcam and self.current_speedcam_distance is not None:
                        self._draw_speedcam_alert_content(draw, self.current_speedcam, self.current_speedcam_distance)
                    
                    # Disegna sempre gli indicatori di connessione (overlay)
                    status = self.current_connection_status
                    self._draw_wifi_indicator(draw, status.get('wifi_connected', False))
                    self._draw_mqtt_indicator(draw, status.get('mqtt_connected', False))
                    self._draw_gps_indicator(draw, status.get('gps_connected', False), status.get('gps_has_fix', False))
                
                # Salva l'immagine corrente per aggiornamenti parziali (DOPO il disegno)
                # Questo deve essere fatto immediatamente per evitare che update_connections_status
                # trovi current_display_image = None e ridisegni tutto
                self._save_current_display()
                
                logger.info(f"✅ Istruzione visualizzata correttamente: {instruction_data.get('instruction', '')[:30]}...")
                
            except Exception as e:
                logger.error(f"❌ Errore visualizzazione istruzione: {e}")
                logger.error(f"   Tipo errore: {type(e).__name__}")
                import traceback
                logger.error(f"   Traceback: {traceback.format_exc()}")
    
    def _should_show_speedcam_overlay(self) -> bool:
        """
        Determina se l'overlay speedcam dovrebbe essere mostrato
        
        Regole:
        - NON mostrare sopra route_overview
        - Sempre mostrare sopra navigation
        - Mostrare sopra idle solo se non c'è un percorso impostato
        
        Returns:
            bool: True se l'overlay dovrebbe essere mostrato
        """
        current_screen = self.display_state.get('current_screen', 'idle')
        
        # NON mostrare mai sopra route_overview
        if current_screen == 'route_overview':
            return False
        
        # Sempre mostrare sopra navigation
        if current_screen == 'navigation':
            return True
        
        # Mostrare sopra idle solo se non c'è un percorso impostato
        if current_screen == 'idle':
            return self.current_route is None
        
        # Default: non mostrare
        return False
    
    def show_speedcam_alert(self, speedcam_data: Dict[str, Any], distance: float):
        """
        Mostra alert speedcam come overlay sopra la schermata corrente
        
        Args:
            speedcam_data: Dati speedcam rilevata
            distance: Distanza dalla speedcam in metri
        """
        if not self.is_initialized:
            logger.error("Display non inizializzato per mostrare alert speedcam")
            return
        
        with self.display_lock:
            try:
                logger.debug(f"Inizio visualizzazione alert speedcam overlay - Distanza: {distance:.0f}m")
                
                # Salva i dati speedcam per poterli ridisegnare se necessario
                self.current_speedcam = speedcam_data
                self.current_speedcam_distance = distance
                
                # NON cambiare current_screen - l'alert è un overlay
                self.display_state['last_update'] = datetime.now()
                
                # Verifica se dovremmo mostrare l'overlay
                if not self._should_show_speedcam_overlay():
                    logger.debug("Overlay speedcam non mostrato: regole di visualizzazione non soddisfatte")
                    return
                
                # Ridisegna la schermata corrente + overlay alert
                with canvas(self.device) as draw:
                    # Prima disegna la schermata corrente
                    current_screen = self.display_state.get('current_screen', 'idle')
                    if current_screen == 'idle':
                        self._draw_idle_content(draw)
                    elif current_screen == 'navigation':
                        self._draw_navigation_content(draw, safe_mode=True)
                    elif current_screen == 'route_overview':
                        self._draw_route_overview_content(draw, safe_mode=True)
                    else:
                        # Default: idle screen
                        self._draw_idle_content(draw)
                    
                    # Poi disegna l'alert sopra (overlay) se permesso
                    if self._should_show_speedcam_overlay():
                        self._draw_speedcam_alert_content(draw, speedcam_data, distance)
                    
                    # Disegna sempre gli indicatori di connessione (overlay)
                    status = self.current_connection_status
                    self._draw_wifi_indicator(draw, status.get('wifi_connected', False))
                    self._draw_mqtt_indicator(draw, status.get('mqtt_connected', False))
                    self._draw_gps_indicator(draw, status.get('gps_connected', False), status.get('gps_has_fix', False))
                
                # Salva l'immagine corrente per aggiornamenti parziali
                self._save_current_display()
                
                logger.debug(f"✅ Alert speedcam overlay visualizzato correttamente - Distanza: {distance:.0f}m")
                
            except Exception as e:
                logger.error(f"❌ Errore visualizzazione alert speedcam: {e}")
                logger.error(f"   Tipo errore: {type(e).__name__}")
                import traceback
                logger.error(f"   Traceback: {traceback.format_exc()}")
    
    def _draw_speedcam_alert_content(self, draw, speedcam_data: Dict[str, Any], distance: float):
        """
        Disegna il contenuto dell'alert speedcam
        
        Args:
            draw: Oggetto canvas per disegnare
            speedcam_data: Dati speedcam
            distance: Distanza dalla speedcam in metri
        """

        # (RI)Carica font se necessario
        if not self.fonts_sys['large'] or not self.fonts_sys['medium'] or not self.fonts_sys['small']:
            self._load_fonts()

        try:
            alert_width = (self.config['width'] / 2)
            alert_height = 160
            
            # Bordo rosso per il riquadro di avviso (colore di allerta)
            # Use rounded_rectangle if available (Pillow >=8.2.0), else fallback to rectangle
            draw.rounded_rectangle([(0, 60), (alert_width, alert_height)], radius=8, fill=self.colors['micronav_red_20'], outline=self.colors['micronav_red'], width=2)
                        
            # Tipo e limite velocità
            speedcam_type = speedcam_data.get('type', '?')
            speedcam_vmax = speedcam_data.get('vmax', '?')
            speedcam_status = speedcam_data.get('status', False)
            
            # Tipo speedcam e stato attivo/inattivo
            type_text = "T RED" if speedcam_type == "A" else "VELOX"
            type_status = "attivo" if speedcam_status else "inattivo"
            
            txt_margin_x = 10
            txt_margin_y = 70
            if self.fonts_sys['medium']:
                bbox = draw.textbbox((0, 0), type_text, font=self.fonts_sys['medium'])
                type_width = bbox[2] - bbox[0]
                draw.text((txt_margin_x, txt_margin_y), type_text, font=self.fonts_sys['medium'], fill=self.colors['white'])

            txt_margin_y = 90
            if self.fonts_sys['small']:
                bbox = draw.textbbox((0, 0), type_status, font=self.fonts_sys['small'])
                type_status_width = bbox[2] - bbox[0]
                draw.text((txt_margin_x, txt_margin_y), type_status, font=self.fonts_sys['small'], fill=self.colors['white'])
            
            # Distanza (in grande)
            distance_text = f"{int(distance)}m"
            txt_margin_y = 110
            if self.fonts_sys['large']:
                bbox = draw.textbbox((0, 0), distance_text, font=self.fonts_sys['large'])
                distance_width = bbox[2] - bbox[0]
                draw.text((txt_margin_x, txt_margin_y), distance_text, font=self.fonts_sys['large'], fill=self.colors['white'])
                        
            # Indicatore visivo (cerchio o simbolo)
            indicator_size = 50
            indicator_x = alert_width - indicator_size // 2 - 10
            indicator_y = 95
            # Disegna cerchio di avviso
            draw.ellipse(
                [(indicator_x - indicator_size // 2, indicator_y - indicator_size // 2),
                 (indicator_x + indicator_size // 2, indicator_y + indicator_size // 2)],
                outline=self.colors['red'],
                fill=self.colors['white'],
                width=5
            )
            # Mostra il limite di velocità (speedcam_vmax) centrato nel cerchio di avviso, se presente e valido
            if speedcam_type == 'A':
                # Mostra l'icona del semaforo nel cerchio per speedcam tipo "A"
                try:
                    import os
                    # path  e dimensioni icona semaforo traffic light
                    traffic_light_path = self.directions_icons_config['icon_traffic_light']
                    icon_width = 15
                    icon_height = 35
                    icon_x = int(indicator_x - icon_width // 2)
                    icon_y = int(indicator_y - icon_height // 2)
                    
                    if os.path.exists(traffic_light_path):
                        with Image.open(traffic_light_path) as traffic_light_image:
                            # Ridimensiona l'immagine a icon_width x icon_height prima di incollarla
                            resized_icon = traffic_light_image.resize((icon_width, icon_height), Image.LANCZOS)
                            draw._image.paste(resized_icon, (icon_x, icon_y))
                    else:
                        logger.error(f"Icona semaforo non trovata: {traffic_light_path}")

                except Exception as e:
                    logger.error(f"Errore caricamento icona semaforo: {e}")

            elif speedcam_type.startswith("G") and speedcam_vmax != '/':
                # mostra Vmax testuale nel cerchio per speedcam tipo "G"
                vmax_text = str(speedcam_vmax)
                if self.fonts_sys['medium']:
                    bbox = draw.textbbox((0, 0), vmax_text, font=self.fonts_sys['medium'])
                    vmax_text_width = bbox[2] - bbox[0]
                    vmax_text_height = bbox[3] - bbox[1]
                    vmax_x = indicator_x - vmax_text_width // 2
                    vmax_y = indicator_y - vmax_text_height
                    draw.text((vmax_x, vmax_y), vmax_text, font=self.fonts_sys['medium'], fill=self.colors['black'])
            else:
                vmax_text = "!"
                if self.fonts_sys['medium']:
                    bbox = draw.textbbox((0, 0), vmax_text, font=self.fonts_sys['medium'])
                    vmax_text_width = bbox[2] - bbox[0]
                    vmax_text_height = bbox[3] - bbox[1]
                    vmax_x = indicator_x - vmax_text_width // 2
                    vmax_y = indicator_y - vmax_text_height
                    draw.text((vmax_x, vmax_y), vmax_text, font=self.fonts_sys['medium'], fill=self.colors['black'])

        except Exception as e:
            logger.error(f"Errore disegno alert speedcam: {e}")
    
    def _draw_wrapped_text(self, draw, text: str, position: Tuple[int, int], 
                          max_width: int, font, color):
        """Disegna testo con a capo automatico"""
        try:
            x, y = position
            words = text.split(' ')
            lines = []
            current_line = []
            
            # Limita la lunghezza del testo per evitare overflow
            max_chars = 35  # Limite caratteri per riga (ridotto per display piccolo)
            if len(text) > max_chars * 4:  # Se troppo lungo, tronca (max 2 righe)
                text = text[:max_chars * 4] + "..."
                words = text.split(' ')
            
            for word in words:
                test_line = ' '.join(current_line + [word])
                bbox = draw.textbbox((0, 0), test_line, font=font)
                text_width = bbox[2] - bbox[0]
                
                if text_width <= max_width:
                    current_line.append(word)
                else:
                    if current_line:
                        lines.append(' '.join(current_line))
                        current_line = [word]
                    else:
                        # Se una singola parola è troppo lunga, troncala
                        if len(word) > max_chars:
                            word = word[:max_chars] + "..."
                        lines.append(word)
            
            if current_line:
                lines.append(' '.join(current_line))
            
            # Limita a 2 righe per evitare overflow verticale
            for i, line in enumerate(lines[:4]):
                draw.text((x, y), line, font=font, fill=color)
                y += 20  # Spaziatura tra righe ottimizzata per display piccolo
            if len(lines) > 4:
                draw.text((x, y), "...", font=font, fill=color)
            
        except Exception as e:
            logger.error(f"Errore disegno testo: {e}")
            # Fallback: mostra solo i primi caratteri
            try:
                short_text = text[:30] + "..." if len(text) > 30 else text
                draw.text(position, short_text, font=font, fill=color)
            except:
                pass
    
    def _get_icon_path(self, maneuver_data: dict) -> str:
        """
        Costruisce il path dell'icona PNG basato sui dati della manovra.
        Segue la convenzione del README: direction_{type}_{modifier}.png
        """
        try:
            maneuver = maneuver_data.get('maneuver', {})
            maneuver_type = maneuver.get('type', '')
            modifier = maneuver.get('modifier', '')
            
            # Costruisce il nome dell'icona secondo la convenzione
            if modifier:
                icon_name = f"direction_{maneuver_type}_{modifier}"
            else:
                icon_name = f"direction_{maneuver_type}"
            
            # Path completo dell'icona PNG
            icon_path = f"{self.directions_icons_config['path']}/{icon_name}.png"
            
            logger.debug(f"Path icona costruito: {icon_path}")
            logger.debug(f"Dati manovra - type: '{maneuver_type}', modifier: '{modifier}'")
            
            return icon_path
            
        except Exception as e:
            logger.error(f"Errore costruzione path icona: {e}")
            return f"{self.directions_icons_config['path']}/direction_close.png"  # Icona di fallback

    def _draw_maneuver_icon(self, draw, icon_path: str, icon_x: int, icon_y: int):
        """Disegna icona manovra PNG"""
        try:
            import os
            
            
            # Verifica se il file esiste
            if not os.path.exists(icon_path):
                logger.warning(f"Icona {icon_path} non trovata: {icon_path}")
                # Disegna icona di fallback
                self._draw_fallback_icon(draw, icon_x, icon_y)
                return
            
            # Carica l'icona PNG
            with Image.open(icon_path) as nav_icon_image:
                logger.debug(f"Immagine caricata: {nav_icon_image.mode} {nav_icon_image.size}")
                
                # Converte immediatamente in RGBA per gestire palette con trasparenza
                if nav_icon_image.mode in ('P', 'L', 'LA'):
                    # Gestisce palette e immagini in scala di grigi con trasparenza
                    nav_icon_image = nav_icon_image.convert('RGBA')
                elif nav_icon_image.mode != 'RGBA':
                    nav_icon_image = nav_icon_image.convert('RGBA')
                
                # Ridimensiona l'icona per il display (24x24 pixel)
                icon_size = self.directions_icons_config['size']
                nav_icon_image = nav_icon_image.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
                
                # Incolla l'icona sul canvas esistente
                if nav_icon_image.mode == 'RGBA':
                    # Usa l'alpha channel come maschera per la trasparenza
                    mask = nav_icon_image.split()[-1]  # Prende il canale alpha
                    draw._image.paste(nav_icon_image, (icon_x, icon_y), mask)
                else:
                    # Se non ha trasparenza, incolla direttamente
                    draw._image.paste(nav_icon_image, (icon_x, icon_y))
                
                logger.debug(f"Icona PNG integrata nel canvas: {icon_path}")


            
        except Exception as e:
            logger.error(f"Errore caricamento icona PNG {icon_path}: {e}")
            # Disegna icona di fallback in caso di errore
            self._draw_fallback_icon(draw, icon_x, icon_y)
    
    def _draw_fallback_icon(self, draw, icon_x: int, icon_y: int):
        """Disegna icona di fallback geometrica"""
        try:
            # Icona generica di fallback
            draw.rectangle((icon_x-10, icon_y-10, icon_x+10, icon_y+10), outline=self.colors['white'], width=2)
            draw.text((icon_x-5, icon_y-5), "?", font=self.fonts_sm['small'], fill=self.colors['white'])
        except Exception as e:
            logger.error(f"Errore disegno icona fallback: {e}")


### Schermate di stato delle connessioni

    def _draw_wifi_indicator(self, draw, connected: bool):

        # (RI)Carica font se necessario
        if not self.fonts_sm['small']:
            self._load_fonts()

        """Disegna indicatore WiFi"""
        try:
            x = 10
            y = 35
            
            if connected:
                # WiFi connesso (verde)
                draw.ellipse((x, y+5, x+5, y+10), fill=self.colors['green'])
                # draw.rectangle((x, y, x+32, y+18), outline=self.colors['light_gray'], width=1)
                draw.text((x+10, y+2), "WiFi", font=self.fonts_sm['small'], fill=self.colors['white'])
            else:
                # WiFi disconnesso (rosso)
                draw.ellipse((x, y+5, x+5, y+10), fill=self.colors['gray'])
                # draw.rectangle((x, y, x+32, y+18), outline=self.colors['gray'], width=1)
                draw.text((x+10, y+2), "WiFi", font=self.fonts_sm['small'], fill=self.colors['gray'])
                
        except Exception as e:
            logger.error(f"Errore indicatore WiFi: {e}")
    
    def _draw_mqtt_indicator(self, draw, connected: bool):

        # (RI)Carica font se necessario
        if not self.fonts_sm['small']:
            self._load_fonts()

        """Disegna indicatore MQTT"""
        try:
            x = 65
            y = 35
            
            if connected:
                # MQTT connesso (verde)
                draw.ellipse((x, y+5, x+5, y+10), fill=self.colors['green'])
                # draw.rectangle((x, y, x+32, y+18), outline=self.colors['light_gray'], width=1)
                draw.text((x+10, y+2), "MQTT", font=self.fonts_sm['small'], fill=self.colors['white'])
            else:
                # MQTT disconnesso (rosso)
                draw.ellipse((x, y+5, x+5, y+10), fill=self.colors['gray'])
                # draw.rectangle((x, y, x+32, y+18), outline=self.colors['gray'], width=1)
                draw.text((x+10, y+2), "MQTT", font=self.fonts_sm['small'], fill=self.colors['gray'])
                
        except Exception as e:
            logger.error(f"Errore indicatore MQTT: {e}")
    
    def _draw_gps_indicator(self, draw, connected: bool, has_fix: bool):

        # (RI)Carica font se necessario
        if not self.fonts_sm['small']:
            self._load_fonts()

        """Disegna indicatore GPS"""
        try:
            x = 120
            y = 35
            
            if connected and has_fix:
                # GPS connesso (verde)
                draw.ellipse((x, y+5, x+5, y+10), fill=self.colors['green'])
                # draw.rectangle((x, y, x+32, y+18), outline=self.colors['white'], width=1)
                draw.text((x+10, y+2), "GPS", font=self.fonts_sm['small'], fill=self.colors['white'])
            elif connected and not has_fix:
                # GPS connesso ma no fix (giallo)
                draw.ellipse((x, y+5, x+5, y+10), fill=self.colors['yellow'])
                # draw.rectangle((x, y, x+32, y+18), outline=self.colors['white'], width=1)
                draw.text((x+10, y+2), "GPS", font=self.fonts_sm['small'], fill=self.colors['light_gray'])
            else:
                # GPS disconnesso (rosso)
                draw.ellipse((x, y+5, x+5, y+10), fill=self.colors['gray'])
                # draw.rectangle((x, y, x+32, y+18), outline=self.colors['gray'], width=1)
                draw.text((x+10, y+2), "GPS", font=self.fonts_sm['small'], fill=self.colors['gray'])
                
        except Exception as e:
            logger.error(f"Errore indicatore MQTT: {e}")

    def update_connections_status(self, wifi_connected: bool, mqtt_connected: bool, gps_connected: bool, gps_has_fix: bool):
        """Aggiorna gli indicatori di connessione come overlay (sempre visibili su tutte le schermate)"""
        if not self.is_initialized:
            return
        
        with self.display_lock:
            try:
                # Salva lo stato corrente delle connessioni (come overlay)
                self.current_connection_status = {
                    'wifi_connected': wifi_connected,
                    'mqtt_connected': mqtt_connected,
                    'gps_connected': gps_connected,
                    'gps_has_fix': gps_has_fix
                }
                
                # Ridisegna la schermata corrente con overlay indicatori
                with canvas(self.device) as draw:
                    # Prima disegna la schermata corrente
                    current_screen = self.display_state.get('current_screen', 'idle')
                    if current_screen == 'idle':
                        self._draw_idle_content(draw)
                    elif current_screen == 'navigation':
                        self._draw_navigation_content(draw, safe_mode=True)
                    elif current_screen == 'route_overview':
                        self._draw_route_overview_content(draw, safe_mode=True)
                    else:
                        self._draw_idle_content(draw)
                    
                    # Disegna overlay speedcam se presente e permesso
                    if (self.current_speedcam and self.current_speedcam_distance is not None and 
                        self._should_show_speedcam_overlay()):
                        self._draw_speedcam_alert_content(draw, self.current_speedcam, self.current_speedcam_distance)
                    
                    # Disegna sempre gli indicatori di connessione (overlay)
                    self._draw_wifi_indicator(draw, wifi_connected)
                    self._draw_mqtt_indicator(draw, mqtt_connected)
                    self._draw_gps_indicator(draw, gps_connected, gps_has_fix)
                
                # Salva l'immagine corrente
                self._save_current_display()
                
            except Exception as e:
                logger.error(f"Errore aggiornamento status connessioni: {e}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
    


### Funzioni di supporto

    def _save_current_display(self):
        """Salva l'immagine corrente del display nel buffer"""
        try:
            # Crea un'immagine temporanea per catturare il contenuto corrente
            temp_image = Image.new('RGB', (self.config['width'], self.config['height']), self.colors['black'])
            temp_draw = ImageDraw.Draw(temp_image)
            
                    # Ridisegna il contenuto corrente basandosi sullo stato
            # Usa i metodi di disegno in modalità safe per evitare ricorsione
            if self.display_state['current_screen'] == 'idle':
                self._draw_idle_content(temp_draw)
            elif self.display_state['current_screen'] == 'navigation':
                # Chiama _draw_navigation_content in modalità safe
                self._draw_navigation_content(temp_draw, safe_mode=True)
            elif self.display_state['current_screen'] == 'route_overview':
                # Chiama _draw_route_overview_content in modalità safe
                self._draw_route_overview_content(temp_draw, safe_mode=True)
            
            # Disegna overlay speedcam se presente e permesso (non cambia current_screen)
            if (self.current_speedcam and self.current_speedcam_distance is not None and 
                self._should_show_speedcam_overlay()):
                self._draw_speedcam_alert_content(temp_draw, self.current_speedcam, self.current_speedcam_distance)
            
            # Disegna sempre gli indicatori di connessione (overlay)
            status = self.current_connection_status
            self._draw_wifi_indicator(temp_draw, status.get('wifi_connected', False))
            self._draw_mqtt_indicator(temp_draw, status.get('mqtt_connected', False))
            self._draw_gps_indicator(temp_draw, status.get('gps_connected', False), status.get('gps_has_fix', False))
            
            # Salva l'immagine nel buffer
            self.current_display_image = temp_image
            return True
        except Exception as e:
            logger.error(f"Errore salvataggio display corrente: {e}")
            return False
    
    def _update_display_from_buffer(self):
        """Aggiorna il display fisico con l'immagine dal buffer"""
        try:
            if self.current_display_image is not None:
                self.device.display(self.current_display_image)
                return True
        except Exception as e:
            logger.error(f"Errore aggiornamento display da buffer: {e}")
        return False
    
    def set_brightness(self, brightness: int):
        """Imposta luminosità display (0-100)"""
        try:
            if 0 <= brightness <= 100:
                # Converti in PWM (0-100 -> 0-1)
                pwm_value = brightness / 100.0
                
                # Controlla backlight via GPIO
                if hasattr(self, 'backlight_pwm'):
                    self.backlight_pwm.ChangeDutyCycle(brightness)
                else:
                    # Crea PWM per backlight
                    GPIO.setup(self.gpio_config['TFT_BL'], GPIO.OUT)
                    self.backlight_pwm = GPIO.PWM(self.gpio_config['TFT_BL'], 1000)
                    self.backlight_pwm.start(brightness)
                
                self.display_state['brightness'] = brightness
                logger.debug(f"Luminosità impostata: {brightness}%")
                
        except Exception as e:
            logger.error(f"Errore impostazione luminosità: {e}")
    
    def start(self):
        """Avvia il controller display"""
        logger.debug("🚀 Avvio Display Controller...")
        
        if not self.initialize_display():
            logger.error("❌ Impossibile inizializzare display")
            return False
        
        self.running = True
        
        # Mostra schermata idle solo se inizializzazione riuscita
        try:
            self.show_idle_screen()
            logger.debug("✅ Display Controller avviato con successo")
            return True
        except Exception as e:
            logger.error(f"❌ Errore schermata idle: {e}")
            # Anche se la schermata idle fallisce, il display è inizializzato
            return True
    
    def update_font_sizes(self):
        """Metodo pubblico per aggiornare le dimensioni dei font"""
        if self.is_initialized:
            logger.info("📝 Aggiornamento dimensioni font richiesto")
            return self.reload_config_and_fonts()
        else:
            logger.warning("⚠️ Display non inizializzato, impossibile aggiornare font")
            return False


    def _ensure_backlight_on(self):
        """Forza il backlight acceso e lo mantiene acceso"""
        try:
            # Riconfigura il pin backlight per essere sicuri
            GPIO.setup(self.gpio_config['TFT_BL'], GPIO.OUT)
            GPIO.output(self.gpio_config['TFT_BL'], GPIO.HIGH)
            
            # Verifica che sia effettivamente acceso
            if GPIO.input(self.gpio_config['TFT_BL']) == GPIO.HIGH:
                logger.debug("💡 Backlight verificato acceso")
            else:
                logger.warning("⚠️ Backlight non risponde correttamente")
                
        except Exception as e:
            logger.error(f"❌ Errore controllo backlight: {e}")
    
    def _ensure_backlight_off(self):
        """Forza il backlight spento"""
        try:
            GPIO.setup(self.gpio_config['TFT_BL'], GPIO.OUT)
            GPIO.output(self.gpio_config['TFT_BL'], GPIO.LOW)
            logger.debug("💡 Backlight spento")
        except Exception as e:
            logger.error(f"❌ Errore spegnimento backlight: {e}")
    
    
    def test_partial_update(self):
        """Test del sistema di aggiornamento parziale"""
        if not self.is_initialized:
            logger.error("Display non inizializzato per test")
            return False
        
        try:
            logger.debug("🧪 Test aggiornamento parziale MQTT...")
            
            # Mostra schermata idle
            self.show_idle_screen()
            time.sleep(1)
            
            # Test aggiornamento MQTT (dovrebbe essere parziale)
            logger.debug("Test MQTT disconnesso...")
            self.update_mqtt_status(False)
            time.sleep(2)
            
            logger.debug("Test MQTT connesso...")
            self.update_mqtt_status(True)
            time.sleep(2)
            
            logger.info("✅ Test aggiornamento parziale completato")
            return True
            
        except Exception as e:
            logger.error(f"❌ Errore test aggiornamento parziale: {e}")
            return False

    def stop(self):
        """Ferma il controller display"""
        self.running = False
        
        if self.is_initialized:
            self.clear_display()
            
            # Spegni backlight
            self._ensure_backlight_off()
            if hasattr(self, 'backlight_pwm'):
                try:
                    self.backlight_pwm.stop()
                except:
                    pass
            
            # Pulisci GPIO
            try:
                GPIO.cleanup()
            except:
                pass
        
        logger.info("✅ Display Controller fermato")