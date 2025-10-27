#!/usr/bin/env python3
"""
MicroNav GPS Controller - L76K GPS Module
Gestisce la comunicazione con il modulo GPS L76K Waveshare
"""

import serial
import time
import threading
import logging
import re
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum

# Configurazione logging
logger = logging.getLogger(__name__)

class GPSStatus(Enum):
    """Stati del GPS"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    FIXING = "fixing"
    FIXED = "fixed"
    ERROR = "error"

@dataclass
class GPSPosition:
    """Dati di posizione GPS"""
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    speed: float = 0.0
    course: float = 0.0
    satellites: int = 0
    hdop: float = 0.0
    fix_quality: int = 0
    timestamp: datetime = None
    is_valid: bool = False
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()

class L76KGPSController:
    """Controller per il modulo GPS L76K Waveshare"""
    
    def __init__(self, port: str = "/dev/ttyS0", baudrate: int = 9600, timeout: float = 2.0):
        """
        Inizializza il controller GPS
        
        Args:
            port: Porta seriale (default: /dev/ttyS0)
            baudrate: Velocità di trasmissione (default: 9600)
            timeout: Timeout per lettura seriale (default: 1.0s)
        """
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        
        # Stato GPS
        self.status = GPSStatus.DISCONNECTED
        self.position = GPSPosition()
        self.last_update = None
        self.fix_timeout = 120  # secondi per ottenere fix
        self.start_time = None
        
        # Threading
        self.serial_connection = None
        self.reading_thread = None
        self.is_running = False
        self.lock = threading.Lock()
        
        # Callbacks
        self.on_position_update = None
        self.on_status_change = None
        
        # Configurazione NMEA (inizializzata dopo i metodi)
        self.nmea_sentences = {}
        
        # Statistiche
        self.stats = {
            'sentences_received': 0,
            'valid_sentences': 0,
            'fix_attempts': 0,
            'last_fix_time': None,
            'uptime': 0
        }
        
        logger.info(f"GPS Controller inizializzato - Porta: {port}, Baudrate: {baudrate}")
    
    def connect(self) -> bool:
        """
        Connette al modulo GPS
        
        Returns:
            bool: True se connessione riuscita
        """
        try:
            logger.info(f"Tentativo connessione GPS su {self.port}...")
            
            self.serial_connection = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS
            )
            
            # Test connessione
            if self.serial_connection.is_open:
                self.status = GPSStatus.CONNECTED
                self.start_time = time.time()
                logger.info("✅ Connessione GPS stabilita")
                
                # Avvia thread di lettura
                self._start_reading_thread()
                return True
            else:
                logger.error("❌ Impossibile aprire connessione seriale")
                return False
                
        except serial.SerialException as e:
            logger.error(f"❌ Errore connessione seriale: {e}")
            self.status = GPSStatus.ERROR
            return False
        except Exception as e:
            logger.error(f"❌ Errore generico connessione GPS: {e}")
            self.status = GPSStatus.ERROR
            return False
    
    def disconnect(self):
        """Disconnette dal modulo GPS"""
        logger.info("Disconnessione GPS...")
        
        self.is_running = False
        
        if self.reading_thread and self.reading_thread.is_alive():
            self.reading_thread.join(timeout=2.0)
        
        if self.serial_connection and self.serial_connection.is_open:
            self.serial_connection.close()
        
        self.status = GPSStatus.DISCONNECTED
        logger.info("GPS disconnesso")
    
    def _start_reading_thread(self):
        """Avvia il thread di lettura dati GPS"""
        if self.reading_thread and self.reading_thread.is_alive():
            return
        
        self.is_running = True
        self.reading_thread = threading.Thread(target=self._reading_loop, daemon=True)
        self.reading_thread.start()
        logger.info("Thread lettura GPS avviato")
    
    def _reading_loop(self):
        """Loop principale di lettura dati GPS"""
        logger.info("Inizio lettura dati GPS...")
        
        while self.is_running and self.serial_connection and self.serial_connection.is_open:
            try:
                # Leggi una riga dal GPS
                line = self.serial_connection.readline().decode('utf-8', errors='ignore').strip()
                
                if line:
                    logger.debug(f"📡 Dati GPS ricevuti: {line}")
                    self._process_nmea_sentence(line)
                else:
                    logger.debug("⏰ Nessun dato GPS ricevuto")
                    
            except serial.SerialTimeoutException:
                # Timeout normale, continua
                logger.debug("⏰ Timeout lettura seriale")
                continue
            except Exception as e:
                logger.error(f"Errore lettura GPS: {e}")
                time.sleep(0.1)
        
        logger.info("Thread lettura GPS terminato")
    
    def _process_nmea_sentence(self, sentence: str):
        """
        Processa una frase NMEA
        
        Args:
            sentence: Frase NMEA da processare
        """
        try:
            # Inizializza dizionario NMEA se non ancora fatto
            if not self.nmea_sentences:
                self._init_nmea_sentences()
            
            # Verifica checksum (disabilitato temporaneamente per debug)
            # if not self._verify_checksum(sentence):
            #     logger.debug(f"Checksum non valido: {sentence}")
            #     return
            
            # Estrai tipo di frase
            if not sentence.startswith('$'):
                return
            
            # Rimuovi $ e estrai comando
            parts = sentence[1:].split('*')
            if len(parts) < 2:
                return
            
            command = parts[0].split(',')[0]
            
            # Processa frase se supportata
            if command in self.nmea_sentences:
                self.stats['sentences_received'] += 1
                logger.debug(f"Processando frase {command}: {sentence}")
                
                try:
                    self.nmea_sentences[command](sentence)
                    self.stats['valid_sentences'] += 1
                    logger.debug(f"Frase {command} processata con successo")
                except Exception as e:
                    logger.debug(f"Errore parsing {command}: {e}")
            else:
                logger.debug(f"Comando {command} non supportato")
            
        except Exception as e:
            logger.debug(f"Errore processamento NMEA: {e}")
    
    def _verify_checksum(self, sentence: str) -> bool:
        """
        Verifica il checksum di una frase NMEA
        
        Args:
            sentence: Frase NMEA
            
        Returns:
            bool: True se checksum valido
        """
        try:
            if '*' not in sentence:
                return False
            
            data, checksum = sentence.split('*')
            calculated_checksum = 0
            
            for char in data[1:]:  # Skip il $
                calculated_checksum ^= ord(char)
            
            return hex(calculated_checksum)[2:].upper().zfill(2) == checksum.upper()
        except:
            return False
    
    def _parse_gpgga(self, sentence: str):
        """Parsa frase GPGGA/GNGGA (Global Positioning System Fix Data)"""
        try:
            parts = sentence.split(',')
            if len(parts) < 15:
                return
            
            # Debug: stampa i campi
            logger.debug(f"GNGGA Parts: {parts}")
            
            # Fix quality (campo 6)
            fix_quality = int(parts[6]) if parts[6] else 0
            
            # Numero satelliti (campo 7)
            satellites = int(parts[7]) if parts[7] else 0
            
            # HDOP (campo 8)
            hdop = float(parts[8]) if parts[8] else 0.0
            
            # Altitudine (campo 9)
            altitude = float(parts[9]) if parts[9] else 0.0
            
            # Latitudine e longitudine se presenti
            latitude = 0.0
            longitude = 0.0
            
            if parts[2] and parts[3] and parts[4] and parts[5]:
                # Latitudine: DDMM.MMMM (campo 2)
                lat_raw = parts[2]
                lat_dir = parts[3]
                if lat_raw and lat_dir:
                    lat_deg = float(lat_raw[:2])
                    lat_min = float(lat_raw[2:])
                    latitude = lat_deg + (lat_min / 60.0)
                    if lat_dir == 'S':
                        latitude = -latitude
                
                # Longitudine: DDDMM.MMMM (campo 4)
                lon_raw = parts[4]
                lon_dir = parts[5]
                if lon_raw and lon_dir:
                    lon_deg = float(lon_raw[:3])
                    lon_min = float(lon_raw[3:])
                    longitude = lon_deg + (lon_min / 60.0)
                    if lon_dir == 'W':
                        longitude = -longitude
            
            with self.lock:
                self.position.latitude = latitude
                self.position.longitude = longitude
                self.position.fix_quality = fix_quality
                self.position.satellites = satellites
                self.position.hdop = hdop
                self.position.altitude = altitude
                self.position.is_valid = fix_quality > 0
                self.last_update = datetime.now()
                
                # Aggiorna stato se abbiamo fix
                if fix_quality > 0:
                    if self.status != GPSStatus.FIXED:
                        self.status = GPSStatus.FIXED
                        self.stats['last_fix_time'] = datetime.now()
                        logger.info(f"🎯 GPS Fix ottenuto! Posizione: {latitude:.6f}, {longitude:.6f}, Satelliti: {satellites}, HDOP: {hdop}")
                        self._notify_status_change()
                else:
                    if self.status == GPSStatus.CONNECTED:
                        self.status = GPSStatus.FIXING
                        self._notify_status_change()
                
                self._notify_position_update()
                
        except Exception as e:
            logger.debug(f"Errore parsing GPGGA: {e}")
    
    def _parse_gprmc(self, sentence: str):
        """Parsa frase GPRMC (Recommended Minimum)"""
        try:
            parts = sentence.split(',')
            if len(parts) < 12:
                return
            
            # Status
            status = parts[2]  # A = Active, V = Void
            
            if status != 'A':
                return
            
            # Latitudine
            lat_raw = parts[3]
            lat_dir = parts[4]
            if lat_raw and lat_dir:
                lat_deg = float(lat_raw[:2])
                lat_min = float(lat_raw[2:])
                latitude = lat_deg + (lat_min / 60.0)
                if lat_dir == 'S':
                    latitude = -latitude
            
            # Longitudine
            lon_raw = parts[5]
            lon_dir = parts[6]
            if lon_raw and lon_dir:
                lon_deg = float(lon_raw[:3])
                lon_min = float(lon_raw[3:])
                longitude = lon_deg + (lon_min / 60.0)
                if lon_dir == 'W':
                    longitude = -longitude
            
            # Velocità
            speed = float(parts[7]) if parts[7] else 0.0
            
            # Corso
            course = float(parts[8]) if parts[8] else 0.0
            
            with self.lock:
                if 'latitude' in locals():
                    self.position.latitude = latitude
                if 'longitude' in locals():
                    self.position.longitude = longitude
                self.position.speed = speed
                self.position.course = course
                self.position.is_valid = True
                self.last_update = datetime.now()
                
                self._notify_position_update()
                
        except Exception as e:
            logger.debug(f"Errore parsing GPRMC: {e}")
    
    def _parse_gpgll(self, sentence: str):
        """Parsa frase GPGLL/GNGLL (Geographic Position - Latitude/Longitude)"""
        try:
            parts = sentence.split(',')
            if len(parts) < 7:
                return
            
            # Status
            status = parts[6]
            if status != 'A':
                return
            
            # Latitudine
            lat_raw = parts[1]
            lat_dir = parts[2]
            if lat_raw and lat_dir:
                lat_deg = float(lat_raw[:2])
                lat_min = float(lat_raw[2:])
                latitude = lat_deg + (lat_min / 60.0)
                if lat_dir == 'S':
                    latitude = -latitude
            
            # Longitudine
            lon_raw = parts[3]
            lon_dir = parts[4]
            if lon_raw and lon_dir:
                lon_deg = float(lon_raw[:3])
                lon_min = float(lon_raw[3:])
                longitude = lon_deg + (lon_min / 60.0)
                if lon_dir == 'W':
                    longitude = -longitude
            
            with self.lock:
                if 'latitude' in locals():
                    self.position.latitude = latitude
                if 'longitude' in locals():
                    self.position.longitude = longitude
                self.position.is_valid = True
                self.last_update = datetime.now()
                
                self._notify_position_update()
                
        except Exception as e:
            logger.debug(f"Errore parsing GPGLL: {e}")
    
    def _parse_gpvtg(self, sentence: str):
        """Parsa frase GPVTG (Track Made Good and Ground Speed)"""
        try:
            parts = sentence.split(',')
            if len(parts) < 10:
                return
            
            # Velocità in km/h
            speed_kmh = float(parts[7]) if parts[7] else 0.0
            
            with self.lock:
                self.position.speed = speed_kmh / 3.6  # Converti in m/s
                self._notify_position_update()
                
        except Exception as e:
            logger.debug(f"Errore parsing GPVTG: {e}")
    
    def _notify_position_update(self):
        """Notifica aggiornamento posizione"""
        if self.on_position_update:
            try:
                self.on_position_update(self.position)
            except Exception as e:
                logger.error(f"Errore callback posizione: {e}")
    
    def _notify_status_change(self):
        """Notifica cambio stato"""
        if self.on_status_change:
            try:
                self.on_status_change(self.status)
            except Exception as e:
                logger.error(f"Errore callback stato: {e}")
    
    def get_position(self) -> GPSPosition:
        """
        Ottiene la posizione corrente
        
        Returns:
            GPSPosition: Posizione GPS corrente
        """
        with self.lock:
            return GPSPosition(
                latitude=self.position.latitude,
                longitude=self.position.longitude,
                altitude=self.position.altitude,
                speed=self.position.speed,
                course=self.position.course,
                satellites=self.position.satellites,
                hdop=self.position.hdop,
                fix_quality=self.position.fix_quality,
                timestamp=self.position.timestamp,
                is_valid=self.position.is_valid
            )
    
    def get_status(self) -> GPSStatus:
        """
        Ottiene lo stato corrente del GPS
        
        Returns:
            GPSStatus: Stato corrente
        """
        return self.status
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Ottiene le statistiche del GPS
        
        Returns:
            Dict: Statistiche GPS
        """
        with self.lock:
            uptime = time.time() - self.start_time if self.start_time else 0
            
            return {
                'status': self.status.value,
                'sentences_received': self.stats['sentences_received'],
                'valid_sentences': self.stats['valid_sentences'],
                'fix_attempts': self.stats['fix_attempts'],
                'last_fix_time': self.stats['last_fix_time'],
                'uptime': uptime,
                'position': {
                    'latitude': self.position.latitude,
                    'longitude': self.position.longitude,
                    'altitude': self.position.altitude,
                    'speed': self.position.speed,
                    'course': self.position.course,
                    'satellites': self.position.satellites,
                    'hdop': self.position.hdop,
                    'fix_quality': self.position.fix_quality,
                    'is_valid': self.position.is_valid,
                    'last_update': self.last_update
                }
            }
    
    def is_connected(self) -> bool:
        """
        Verifica se il GPS è connesso
        
        Returns:
            bool: True se connesso
        """
        return (self.serial_connection and 
                self.serial_connection.is_open and 
                self.status != GPSStatus.DISCONNECTED)
    
    def has_fix(self) -> bool:
        """
        Verifica se il GPS ha un fix valido
        
        Returns:
            bool: True se ha fix
        """
        return (self.status == GPSStatus.FIXED and 
                self.position.is_valid and 
                self.position.fix_quality > 0)
    
    def wait_for_fix(self, timeout: int = 60) -> bool:
        """
        Attende un fix GPS
        
        Args:
            timeout: Timeout in secondi
            
        Returns:
            bool: True se fix ottenuto
        """
        logger.info(f"Attesa fix GPS (timeout: {timeout}s)...")
        
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.has_fix():
                logger.info("✅ Fix GPS ottenuto!")
                return True
            
            time.sleep(1)
        
        logger.warning("⏰ Timeout attesa fix GPS")
        return False
    
    def send_command(self, command: str) -> bool:
        """
        Invia comando al GPS
        
        Args:
            command: Comando da inviare
            
        Returns:
            bool: True se comando inviato
        """
        if not self.is_connected():
            logger.error("GPS non connesso")
            return False
        
        try:
            # Aggiungi terminatore se necessario
            if not command.endswith('\r\n'):
                command += '\r\n'
            
            self.serial_connection.write(command.encode())
            logger.debug(f"Comando inviato: {command.strip()}")
            return True
            
        except Exception as e:
            logger.error(f"Errore invio comando: {e}")
            return False
    
    def _init_nmea_sentences(self):
        """Inizializza il dizionario delle frasi NMEA"""
        self.nmea_sentences = {
            'GPGGA': self._parse_gpgga,
            'GNGGA': self._parse_gpgga,  # Aggiungi supporto GNGGA
            'GPRMC': self._parse_gprmc,
            'GNRMC': self._parse_gprmc,  # Aggiungi supporto GNRMC
            'GPGLL': self._parse_gpgll,
            'GNGLL': self._parse_gpgll,  # Aggiungi supporto GNGLL
            'GPVTG': self._parse_gpvtg,
            'GNVTG': self._parse_gpvtg   # Aggiungi supporto GNVTG
        }
        
        # Debug: stampa i comandi supportati
        logger.debug(f"Comandi NMEA supportati: {list(self.nmea_sentences.keys())}")
    
    def configure_gps(self):
        """Configura il modulo GPS con parametri ottimali"""
        logger.info("Configurazione GPS...")
        
        # Comandi di configurazione
        commands = [
            # Abilita solo frasi essenziali
            "$PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0*28",
            # Imposta frequenza aggiornamento a 1Hz
            "$PMTK220,1000*1F",
            # Abilita SBAS
            "$PMTK313,1*2E",
            # Imposta modalità di navigazione
            "$PMTK386,0.2*3C"
        ]
        
        for cmd in commands:
            if self.send_command(cmd):
                time.sleep(0.5)
        
        logger.info("Configurazione GPS completata")

# Funzioni di utilità
def format_coordinates(latitude: float, longitude: float) -> str:
    """
    Formatta coordinate in formato leggibile
    
    Args:
        latitude: Latitudine
        longitude: Longitudine
        
    Returns:
        str: Coordinate formattate
    """
    lat_dir = "N" if latitude >= 0 else "S"
    lon_dir = "E" if longitude >= 0 else "W"
    
    lat_deg = int(abs(latitude))
    lat_min = (abs(latitude) - lat_deg) * 60
    
    lon_deg = int(abs(longitude))
    lon_min = (abs(longitude) - lon_deg) * 60
    
    return f"{lat_deg}°{lat_min:.3f}'{lat_dir} {lon_deg}°{lon_min:.3f}'{lon_dir}"

def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calcola distanza tra due punti (formula di Haversine)
    
    Args:
        lat1, lon1: Prima posizione
        lat2, lon2: Seconda posizione
        
    Returns:
        float: Distanza in metri
    """
    import math
    
    R = 6371000  # Raggio Terra in metri
    
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    
    a = (math.sin(delta_lat / 2) ** 2 + 
         math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c

if __name__ == "__main__":
    # Test del modulo GPS
    import sys
    
    # Configurazione logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    def on_position_update(position: GPSPosition):
        """Callback per aggiornamento posizione"""
        print(f"📍 Posizione: {format_coordinates(position.latitude, position.longitude)}")
        print(f"   Altitudine: {position.altitude:.1f}m")
        print(f"   Velocità: {position.speed:.1f}m/s")
        print(f"   Satelliti: {position.satellites}")
        print(f"   HDOP: {position.hdop:.1f}")
        print()
    
    def on_status_change(status: GPSStatus):
        """Callback per cambio stato"""
        print(f"🔄 Stato GPS: {status.value}")
    
    # Crea controller GPS
    gps = L76KGPSController()
    gps.on_position_update = on_position_update
    gps.on_status_change = on_status_change
    
    try:
        # Connetti
        if gps.connect():
            print("✅ GPS connesso")
            
            # Configura
            gps.configure_gps()
            
            # Attendi fix
            if gps.wait_for_fix(timeout=60):
                print("✅ Fix ottenuto!")
                
                # Mostra posizione per 30 secondi
                for i in range(30):
                    if gps.has_fix():
                        pos = gps.get_position()
                        print(f"📍 {format_coordinates(pos.latitude, pos.longitude)}")
                    time.sleep(1)
            else:
                print("❌ Fix non ottenuto")
        
    except KeyboardInterrupt:
        print("\n⏹️  Interruzione utente")
    finally:
        gps.disconnect()
        print("🔌 GPS disconnesso")
