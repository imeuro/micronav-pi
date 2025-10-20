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
        self.display_thread = None
        self.running = False
        
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
            'brightness': 100,
            'orientation': 0,
            'current_screen': 'idle',
            'last_update': None
        }
        
        # Cache immagini
        self.icon_cache = {}
        
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
            logger.info("🔄 Ricaricamento configurazione e font...")
            
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
        logger.info("🔧 Inizializzazione display ST7789...")
        
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
            logger.info("🔄 Reset display...")
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
            logger.info("✅ SPI configurato")
            
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
            logger.info("✅ Dispositivo ST7789 creato")
            
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
            logger.info("✅ Display TFT ST7789 inizializzato con successo")
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
            logger.info("Test display completato")
            
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
    
    def show_idle_screen(self):
        """Mostra schermata di attesa"""
        if not self.is_initialized:
            return
        
        try:
            logger.debug("Mostrando schermata idle")
            
            with canvas(self.device) as draw:
                import os
                logo_path = '/home/micronav/micronav-assets/micronav.png'


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
                        (self.config['width'] // 2 - 70, 90),
                        "MicroNav",
                        font=self.fonts_sys['large'],
                        fill=self.colors['white']
                    )
                    
                
                # Status
                draw.text(
                    (self.config['width'] // 2 - 60, 140),
                    "In attesa...",
                    font=self.fonts_sys['medium'],
                    fill=self.colors['gray']
                )
                
                # Indicatore WiFi
                self._draw_wifi_indicator(draw, True)
                # Indicatore MQTT
                self._draw_mqtt_indicator(draw, True)
                # Indicatore GPS
                self._draw_gps_indicator(draw, False)
            
            self.display_state['current_screen'] = 'idle'
            self.display_state['last_update'] = datetime.now()

            time.sleep(3)
            
        except Exception as e:
            logger.error(f"Errore schermata idle: {e}")
    
    def show_navigation_instruction(self, instruction_data: Dict[str, Any]):
        """Mostra istruzione di navigazione"""
        if not self.is_initialized:
            logger.error("Display non inizializzato per mostrare istruzione")
            return
        
        try:
            # Pulisci lo schermo prima di mostrare l'istruzione
            logger.debug("Pulizia schermo prima di istruzione")
            self.clear_display()
            time.sleep(0.1)  # Piccola pausa per assicurarsi che la pulizia sia completata
            
            logger.debug(f"Inizio visualizzazione istruzione: {instruction_data.get('instruction', '')[:30]}...")
            instruction = instruction_data.get('instruction', '')
            distance = instruction_data.get('distance', 0)
            duration = instruction_data.get('duration', 0)
            maneuver = instruction_data.get('maneuver', {})
            icon = instruction_data.get('icon', '')
            
            with canvas(self.device) as draw:
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
                
                # Indicatori di stato
                self._draw_wifi_indicator(draw, True)
                self._draw_mqtt_indicator(draw, True)
                self._draw_gps_indicator(draw, False)
            
            self.current_instruction = instruction_data
            self.display_state['current_screen'] = 'navigation'
            self.display_state['last_update'] = datetime.now()
            
            logger.info(f"✅ Istruzione visualizzata correttamente: {instruction[:30]}...")
            
        except Exception as e:
            logger.error(f"❌ Errore visualizzazione istruzione: {e}")
            logger.error(f"   Tipo errore: {type(e).__name__}")
            import traceback
            logger.error(f"   Traceback: {traceback.format_exc()}")
    
    def show_route_overview(self, route_data: Dict[str, Any]):
        """Mostra panoramica del percorso"""
        if not self.is_initialized:
            logger.error("Display non inizializzato per mostrare panoramica percorso")
            return
        
        try:
            # Pulisci lo schermo prima di mostrare la panoramica
            logger.debug("Pulizia schermo prima di panoramica")
            self.clear_display()
            time.sleep(0.3)  # Piccola pausa per assicurarsi che la pulizia sia completata
            origin = route_data.get('origin', '')
            destination = route_data.get('destination', '')
            total_distance = route_data.get('totalDistance', 0)
            total_duration = route_data.get('totalDuration', 0)
            steps = route_data.get('steps', [])
            
            logger.debug(f"Mostrando panoramica: origine='{origin[:50]}...', destinazione='{destination[:50]}...'")
            
            # Verifica che i font siano caricati
            if not self.fonts_sm['small'] or not self.fonts_sm['large']:
                logger.warning("Font non caricati correttamente, uso font predefinito")
                self.fonts_sm['small'] = ImageFont.load_default()
                self.fonts_sm['large'] = ImageFont.load_default()
            
            with canvas(self.device) as draw:
                # Sfondo
                draw.rectangle(
                    (0, 0, self.config['width'], self.config['height']),
                    fill=self.colors['black']
                )
                
                # Titolo
                draw.text(
                    (10, 5),
                    "Percorso",
                    font=self.fonts_sm['large'],
                    fill=self.colors['white']
                )
                
                # Origine
                draw.text(
                    (10, 35),
                    "Da:",
                    font=self.fonts_sys['medium'],
                    fill=self.colors['light_gray']
                )
                # Tronca l'origine se troppo lunga
                origin_short = origin[:50] + "..." if len(origin) > 50 else origin
                self._draw_wrapped_text(
                    draw,
                    origin_short,
                    (10, 50),
                    self.config['width'] - 20,
                    self.fonts_sys['medium'],
                    self.colors['white']
                )
                
                # Destinazione
                draw.text(
                    (10, 90),
                    "A:",
                    font=self.fonts_sys['medium'],
                    fill=self.colors['light_gray']
                )
                # Tronca la destinazione se troppo lunga
                destination_short = destination[:50] + "..." if len(destination) > 50 else destination
                self._draw_wrapped_text(
                    draw,
                    destination_short,
                    (10, 105),
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
                        (self.config['width'] - 10 - draw.textlength(f"{duration_text}", font=self.fonts_sys['medium']), 160),
                        f"{duration_text}",
                        font=self.fonts_sys['medium'],
                        fill=self.colors['light_gray']
                    )
            
            self.current_route = route_data
            self.display_state['current_screen'] = 'route_overview'
            self.display_state['last_update'] = datetime.now()
            
            logger.info(f"Panoramica percorso visualizzata: {origin} → {destination}")
            
        except Exception as e:
            logger.error(f"Errore visualizzazione panoramica: {e}")
    
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
            logger.info(f"Dati manovra - type: '{maneuver_type}', modifier: '{modifier}'")
            
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
    
    def _draw_wifi_indicator(self, draw, connected: bool):
        """Disegna indicatore WiFi"""
        try:
            x = 10
            y = 35
            
            if connected:
                # WiFi connesso (verde)
                draw.ellipse((x, y+5, x+5, y+10), fill=self.colors['green'])
                # draw.rectangle((x, y, x+32, y+18), outline=self.colors['light_gray'], width=1)
                draw.text((x+8, y+2), "WiFi", font=self.fonts_sm['small'], fill=self.colors['white'])
            else:
                # WiFi disconnesso (rosso)
                draw.ellipse((x, y+5, x+5, y+10), fill=self.colors['gray'])
                # draw.rectangle((x, y, x+32, y+18), outline=self.colors['gray'], width=1)
                draw.text((x+8, y+2), "WiFi", font=self.fonts_sm['small'], fill=self.colors['gray'])
                
        except Exception as e:
            logger.error(f"Errore indicatore WiFi: {e}")
    
    def _draw_mqtt_indicator(self, draw, connected: bool):
        """Disegna indicatore MQTT"""
        try:
            x = 60
            y = 35
            
            if connected:
                # MQTT connesso (verde)
                draw.ellipse((x, y+5, x+5, y+10), fill=self.colors['green'])
                # draw.rectangle((x, y, x+32, y+18), outline=self.colors['light_gray'], width=1)
                draw.text((x+8, y+2), "MQTT", font=self.fonts_sm['small'], fill=self.colors['white'])
            else:
                # MQTT disconnesso (rosso)
                draw.ellipse((x, y+5, x+5, y+10), fill=self.colors['gray'])
                # draw.rectangle((x, y, x+32, y+18), outline=self.colors['gray'], width=1)
                draw.text((x+8, y+2), "MQTT", font=self.fonts_sm['small'], fill=self.colors['gray'])
                
        except Exception as e:
            logger.error(f"Errore indicatore MQTT: {e}")
    
    def _draw_gps_indicator(self, draw, connected: bool):
        """Disegna indicatore GPS"""
        try:
            x = 110
            y = 35
            
            if connected:
                # GPS connesso (verde)
                draw.ellipse((x, y+5, x+5, y+10), fill=self.colors['green'])
                # draw.rectangle((x, y, x+32, y+18), outline=self.colors['white'], width=1)
                draw.text((x+8, y+2), "GPS", font=self.fonts_sm['small'], fill=self.colors['white'])
            else:
                # GPS disconnesso (rosso)
                draw.ellipse((x, y+5, x+5, y+10), fill=self.colors['gray'])
                # draw.rectangle((x, y, x+32, y+18), outline=self.colors['gray'], width=1)
                draw.text((x+8, y+2), "GPS", font=self.fonts_sm['small'], fill=self.colors['gray'])
                
        except Exception as e:
            logger.error(f"Errore indicatore MQTT: {e}")
    
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
            logger.info("✅ Display Controller avviato con successo")
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

# Esempio di utilizzo
if __name__ == "__main__":
    # Crea controller display
    display = MicroNavDisplayController()
    
    try:
        # Avvia display
        if display.start():
            print("✅ Display avviato. Premi Ctrl+C per fermare.")
            
            # Test schermate
            time.sleep(2)
            
            # Test istruzione - Turn Right
            test_instruction = {
                'instruction': 'Gira a destra su Via Roma',
                'distance': 150,
                'duration': 30,
                'maneuver': {'type': 'turn', 'modifier': 'right'},
                'icon': 'turn_right'
            }
            display.show_navigation_instruction(test_instruction)
            
            time.sleep(3)
            
            # Test istruzione - Turn Left
            test_instruction_left = {
                'instruction': 'Gira a sinistra su Via Torino',
                'distance': 200,
                'duration': 40,
                'maneuver': {'type': 'turn', 'modifier': 'left'},
                'icon': 'turn_left'
            }
            display.show_navigation_instruction(test_instruction_left)
            
            time.sleep(3)
            
            # Test istruzione - Continue (senza modifier)
            test_instruction_continue = {
                'instruction': 'Continua dritto su Via Nazionale',
                'distance': 500,
                'duration': 60,
                'maneuver': {'type': 'continue'},
                'icon': 'straight'
            }
            display.show_navigation_instruction(test_instruction_continue)
            
            time.sleep(3)
            
            # Test istruzione - U-turn
            test_instruction_uturn = {
                'instruction': 'Fai inversione a U',
                'distance': 50,
                'duration': 15,
                'maneuver': {'type': 'turn', 'modifier': 'uturn'},
                'icon': 'uturn'
            }
            display.show_navigation_instruction(test_instruction_uturn)
            
            time.sleep(3)
            
            # Test panoramica percorso
            test_route = {
                'origin': 'Via Milano, 123',
                'destination': 'Piazza Duomo',
                'totalDistance': 2500,
                'totalDuration': 300,
                'steps': [{'instruction': 'Gira a destra'}, {'instruction': 'Vai dritto'}]
            }
            display.show_route_overview(test_route)
            
            time.sleep(3)
            
            # Torna a idle
            display.show_idle_screen()
            
            # Loop principale
            while True:
                time.sleep(1)
        else:
            print("❌ Errore avvio display")
    
    except KeyboardInterrupt:
        print("\n⏹️  Arresto display...")
        display.stop()
        print("✅ Display fermato")
    
    except Exception as e:
        print(f"❌ Errore: {e}")
        display.stop()

