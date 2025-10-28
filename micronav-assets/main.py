#!/usr/bin/env python3
"""
MicroNav Raspberry Pi - Main Orchestrator
Coordina tutti i componenti del sistema: MQTT, Display, WiFi
"""

import time
import signal
import sys
import logging
import threading
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

# Import componenti
from config import get_config, validate_config
from mqtt_client import MicroNavMQTTClient
from display_controller import MicroNavDisplayController
from gps_controller import L76KGPSController, GPSPosition, GPSStatus
# from wifi_monitor import MicroNavWiFiMonitor

# Configurazione logging
logger = logging.getLogger(__name__)

# Configura il logging solo se non è già configurato
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('/home/micronav/micronav-pi/micronav-assets/logs/micronav.log'),
            logging.StreamHandler(sys.stdout)
        ]
    )

class MicroNavSystem:
    """Sistema principale MicroNav Raspberry Pi"""
    
    # Variabile di classe per tracciare le istanze
    _instances = []
    
    def __init__(self):
        """Inizializza il sistema MicroNav"""
        # Controlla se esiste già un'istanza
        if len(self._instances) > 0:
            logger.error(f"❌ Tentativo di creare istanza sistema multipla! Esistono già {len(self._instances)} istanze")
            # Non creare una nuova istanza, usa quella esistente
            existing = self._instances[0]
            self.__dict__.update(existing.__dict__)
            return
        
        self.config = None
        self.mqtt_client = None
        self.display_controller = None
        self.gps_controller = None
        self.wifi_monitor = None
        
        # Stato sistema
        self.is_running = False
        self.start_time = None
        self.last_heartbeat = None
        self.current_route = None
        self.current_position = None
        self.system_stats = {
            'uptime': 0,
            'mqtt_connected': False,
            'wifi_connected': False,
            'gps_connected': False,
            'gps_fix': False,
            'display_active': False,
            'messages_received': 0,
            'errors_count': 0,
            'last_route_time': None,
            'last_instruction_time': None
        }
        
        # Thread di monitoraggio
        self.monitor_thread = None
        self.heartbeat_thread = None
        
        # Gestione segnali
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Aggiungi questa istanza alla lista
        self._instances.append(self)
        
        logger.info(f"Sistema MicroNav inizializzato (ID: {id(self)}) - Istanza {len(self._instances)}")
    
    def initialize(self) -> bool:
        """Inizializza tutti i componenti del sistema in parallelo"""
        if self.is_running:
            logger.warning("⚠️  Sistema già inizializzato, salto inizializzazione")
            return True
            
        try:
            logger.info("🚀 Avvio inizializzazione sistema MicroNav...")
            
            # Carica configurazione
            self.config = get_config()
            if not validate_config():
                logger.error("❌ Configurazione non valida")
                return False
            
            logger.info("✅ Configurazione caricata")
            
            # Inizializza display controller PRIMA (priorità massima)
            logger.debug("📱 Inizializzazione display (priorità alta)...")
            self.display_controller = MicroNavDisplayController()
            if not self.display_controller.start():
                logger.error("❌ Errore inizializzazione display")
                return False
            
            logger.info("✅ Display inizializzato")
            
            # Inizializza GPS controller
            logger.debug("🛰️  Inizializzazione GPS controller...")
            try:
                gps_config = self.config['gps']
                self.gps_controller = L76KGPSController(
                    port=gps_config['port'],
                    baudrate=gps_config['baudrate'],
                    timeout=gps_config['timeout']
                )
                
                # Imposta callbacks
                self.gps_controller.on_position_update = self._on_gps_position_update
                self.gps_controller.on_status_change = self._on_gps_status_change
                
                # Connetti al GPS
                if self.gps_controller.connect():
                    logger.info("✅ GPS controller connesso")
                    self.system_stats['gps_connected'] = True
                    
                    # Configura GPS se abilitato
                    if gps_config.get('auto_configure', True):
                        self.gps_controller.configure_gps()
                        logger.info("✅ GPS configurato automaticamente")
                else:
                    logger.warning("⚠️  GPS non connesso - continuo senza GPS")
                    self.system_stats['gps_connected'] = False
                    
            except Exception as e:
                logger.error(f"❌ Errore inizializzazione GPS: {e}")
                self.gps_controller = None
                self.system_stats['gps_connected'] = False
            
            # WiFi monitor disabilitato
            logger.info("📶 WiFi monitor disabilitato")
            self.wifi_monitor = None
            
            # Inizializza MQTT client in parallelo
            logger.info("📡 Inizializzazione MQTT client in parallelo...")
            
            # Controlla se esiste già un'istanza
            existing_client = MicroNavMQTTClient.get_instance()
            if existing_client is not None:
                logger.warning("⚠️  MQTT client già esistente, uso istanza esistente...")
                self.mqtt_client = existing_client
            else:
                if self.mqtt_client is not None:
                    logger.warning("⚠️  MQTT client locale già esistente, disconnetto prima...")
                    self.mqtt_client.stop()
                self.mqtt_client = MicroNavMQTTClient(self.config['mqtt'])
            
            # Assegna riferimento display_controller al MQTT client, per aggiornamento stato connessioni
            if self.mqtt_client and self.display_controller:
                self.mqtt_client.display_controller = self.display_controller
                logger.info("🔗 Display controller collegato al MQTT client")
            
            # Registra handler per messaggi MQTT
            self._register_mqtt_handlers()
            
            # Avvia MQTT in thread separato per non bloccare
            mqtt_thread = threading.Thread(target=self._initialize_mqtt_async, daemon=True)
            mqtt_thread.start()
            
            # Aggiorna stato sistema
            self.system_stats['display_active'] = True
            self.system_stats['wifi_connected'] = True  # Assume sempre connesso
            
            logger.info("🎉 Sistema MicroNav inizializzato con successo!")
            return True
            
        except Exception as e:
            logger.error(f"❌ Errore inizializzazione sistema: {e}")
            return False
    
    def _initialize_mqtt_async(self):
        """Inizializza MQTT client in modo asincrono"""
        try:
            logger.info("📡 Avvio MQTT client in background...")
            if self.mqtt_client.start():
                self.system_stats['mqtt_connected'] = True
                logger.info("✅ MQTT client inizializzato in background")
                # Aggiorna statistiche MQTT
                self._update_mqtt_system_stats()
            else:
                logger.error("❌ Errore inizializzazione MQTT client in background")
                self.system_stats['mqtt_connected'] = False
        except Exception as e:
            logger.error(f"❌ Errore MQTT asincrono: {e}")
            self.system_stats['mqtt_connected'] = False
    
    def _safe_publish_status(self, topic: str, status_type: str, message: str, data: Dict[str, Any] = None):
        """Pubblica status MQTT in modo sicuro"""
        try:
            if self.mqtt_client and hasattr(self.mqtt_client, 'is_connected') and self.mqtt_client.is_connected:
                self.mqtt_client.publish_status(topic, status_type, message, data)
            else:
                logger.debug(f"MQTT non connesso, salto pubblicazione: {status_type}")
        except Exception as e:
            logger.error(f"Errore pubblicazione MQTT {status_type}: {e}")
    
    def _update_mqtt_system_stats(self):
        """Aggiorna le statistiche del sistema nel MQTT client"""
        try:
            if self.mqtt_client and hasattr(self.mqtt_client, 'update_system_stats'):
                # Prepara le statistiche da inviare al MQTT client
                mqtt_stats = {
                    'gps_fix': self.system_stats.get('gps_fix', False),
                    'gps_connected': self.system_stats.get('gps_connected', False),
                    'wifi_connected': self.system_stats.get('wifi_connected', False),
                    'mqtt_connected': self.system_stats.get('mqtt_connected', False)
                }
                self.mqtt_client.update_system_stats(mqtt_stats)
        except Exception as e:
            logger.error(f"Errore aggiornamento statistiche MQTT: {e}")

    def _register_mqtt_handlers(self):
        """Registra handler per messaggi MQTT"""
        try:
            # Handler per dati percorso completo
            self.mqtt_client.register_message_handler(
                "route/data",
                self._handle_route_data
            )
            
            # Handler per istruzioni di navigazione
            self.mqtt_client.register_message_handler(
                "route/step",
                self._handle_navigation_step
            )
            
            # Handler per comandi sistema
            self.mqtt_client.register_message_handler(
                "commands",
                self._handle_system_commands
            )
            
            # Handler per posizione GPS
            self.mqtt_client.register_message_handler(
                "position",
                self._handle_gps_position
            )
            
            logger.info("✅ Handler MQTT registrati")
            
        except Exception as e:
            logger.error(f"Errore registrazione handler MQTT: {e}")
        
    def _on_gps_position_update(self, position: GPSPosition):
        """Callback per aggiornamento posizione GPS"""
        try:
            # Aggiorna posizione corrente
            self.current_position = position
            
            # Aggiorna stato sistema
            self.system_stats['gps_fix'] = position.is_valid and position.fix_quality > 0
            
            # Aggiorna statistiche MQTT
            self._update_mqtt_system_stats()
            
            # Throttling: invia posizione GPS solo ogni 3 secondi
            current_time = time.time()
            if not hasattr(self, '_last_gps_publish_time'):
                self._last_gps_publish_time = 0
            
            if current_time - self._last_gps_publish_time >= 3.0:
                # Pubblica posizione GPS via MQTT
                if self.mqtt_client and hasattr(self.mqtt_client, 'is_connected') and self.mqtt_client.is_connected:
                    gps_data = {
                        'latitude': position.latitude,
                        'longitude': position.longitude,
                        'altitude': position.altitude,
                        'speed': position.speed,
                        'course': position.course,
                        'satellites': position.satellites,
                        'hdop': position.hdop,
                        'fix_quality': position.fix_quality,
                        'timestamp': position.timestamp.isoformat() if position.timestamp else None,
                        'is_valid': position.is_valid
                    }
                
                    # Aggiorna timestamp ultimo invio
                    self._last_gps_publish_time = current_time
                    
                    # Log posizione se valida (solo quando inviamo)
                    if position.is_valid and position.fix_quality > 0:
                        self._safe_publish_status(
                            self.mqtt_client.topics['publish']['gps_position'],
                            "gps_position",
                            "Posizione GPS aggiornata",
                            gps_data
                        )
                    logger.info(f"📍 GPS: {position.latitude:.6f}, {position.longitude:.6f} "
                               f"(Sat: {position.satellites}, HDOP: {position.hdop:.1f})")
            else:
                # Log debug per posizioni non inviate (solo se valide)
                if position.is_valid and position.fix_quality > 0:
                    logger.debug(f"📍 GPS (throttled): {position.latitude:.6f}, {position.longitude:.6f} "
                               f"(Sat: {position.satellites}, HDOP: {position.hdop:.1f})")
            
        except Exception as e:
            logger.error(f"Errore callback GPS posizione: {e}")
    
    def _on_gps_status_change(self, status: GPSStatus):
        """Callback per cambio stato GPS"""
        try:
            logger.info(f"🛰️  GPS Status: {status.value}")
            
            # Aggiorna stato sistema
            if status == GPSStatus.FIXED:
                self.system_stats['gps_fix'] = True
            elif status in [GPSStatus.DISCONNECTED, GPSStatus.ERROR]:
                self.system_stats['gps_fix'] = False
                self.system_stats['gps_connected'] = False
            
            # Aggiorna statistiche MQTT
            self._update_mqtt_system_stats()
            
            # Pubblica status GPS via MQTT
            self._safe_publish_status(
                "gps_status",
                f"GPS {status.value}",
                f"GPS {status.value}",
                {
                    'status': status.value,
                    'has_fix': self.system_stats['gps_fix'],
                    'connected': self.system_stats['gps_connected']
                }
            )
            
        except Exception as e:
            logger.error(f"Errore callback GPS status: {e}")
    
    def _handle_route_data(self, topic: str, data: Dict[str, Any]):
        """Gestisce dati percorso completo"""
        try:
            logger.info(f"📍 Percorso ricevuto: {data.get('origin', 'N/A')} → {data.get('destination', 'N/A')}")
            
            # Pulisci il display prima di mostrare il nuovo percorso
            logger.debug("Pulizia display per nuovo percorso")
            self.display_controller.clear_display()
            time.sleep(0.5)

            # Mostra panoramica percorso sul display
            logger.debug("Mostrando panoramica percorso")
            self.display_controller.show_route_overview(data)
            
            # Salva il percorso per riferimento
            self.current_route = data
            
            # Aggiorna statistiche
            self.system_stats['messages_received'] += 1
            self.system_stats['last_route_time'] = datetime.now()
            
            # Pubblica conferma ricezione
            self._safe_publish_status(
                f"micronav/pwa/{self.config['mqtt']['device_id']}/actions",
                "navigating",
                "Percorso ricevuto e visualizzato",
                {"route_id": data.get('id', 'unknown')}
            )
            
            # Mostra automaticamente la prima istruzione dopo 3 secondi
            self._show_first_instruction_after_delay(data)
            
        except Exception as e:
            logger.error(f"Errore gestione percorso: {e}")
            self.system_stats['errors_count'] += 1
            
            # Fallback: torna alla schermata idle in caso di errore
            try:
                logger.warning("Tentativo di tornare alla schermata idle dopo errore")
                self.display_controller.clear_display()
                time.sleep(0.3)
                self.display_controller.show_idle_screen()
            except Exception as fallback_error:
                logger.error(f"Errore anche nel fallback idle: {fallback_error}")
    
    def _handle_navigation_step(self, topic: str, data: Dict[str, Any]):
        """Gestisce istruzione di navigazione"""
        try:
            instruction = data.get('instruction', '')
            logger.info(f"🧭 Istruzione: {instruction[:50]}...")
            
            # Pulisci il display prima di mostrare la nuova istruzione
            logger.debug("Pulizia display per nuova istruzione")
            self.display_controller.clear_display()
            time.sleep(0.3)  # Pausa per assicurarsi che la pulizia sia completata
            
            # Mostra istruzione sul display
            logger.debug("Mostrando istruzione di navigazione")
            self.display_controller.show_navigation_instruction(data)
            
            # Aggiorna statistiche
            self.system_stats['messages_received'] += 1
            self.system_stats['last_instruction_time'] = datetime.now()
            
            # Pubblica conferma visualizzazione
            self._safe_publish_status(
                f"micronav/pwa/{self.config['mqtt']['device_id']}/actions",
                "navigating",
                "Istruzione visualizzata",
                {"instruction_id": data.get('id', 'unknown')}
            )
            
        except Exception as e:
            logger.error(f"Errore gestione istruzione: {e}")
            self.system_stats['errors_count'] += 1
            
            # Fallback: torna alla schermata idle in caso di errore
            try:
                logger.warning("Tentativo di tornare alla schermata idle dopo errore istruzione")
                self.display_controller.clear_display()
                time.sleep(0.3)
                self.display_controller.show_idle_screen()
            except Exception as fallback_error:
                logger.error(f"Errore anche nel fallback idle: {fallback_error}")
    
    def _show_first_instruction_after_delay(self, route_data: Dict[str, Any]):
        """Mostra la prima istruzione dopo un delay"""
        def show_first_instruction():
            try:
                # Attendi 3 secondi per mostrare la panoramica
                time.sleep(10)
                
                # Estrai la prima istruzione dal percorso
                steps = route_data.get('steps', [])
                if steps and len(steps) > 0:
                    first_step = steps[0]
                    
                    # Crea dati istruzione compatibili
                    instruction_data = {
                        'instruction': first_step.get('instruction', 'Inizia la navigazione'),
                        'distance': first_step.get('distance', 0),
                        'duration': first_step.get('duration', 0),
                        'maneuver': first_step.get('maneuver', {}),
                        'icon': first_step.get('icon', '')
                    }
                    
                    logger.info(f"🧭 Mostro prima istruzione: {instruction_data['instruction'][:50]}...")
                    
                    # Mostra la prima istruzione
                    self.display_controller.show_navigation_instruction(instruction_data)
                    
                    # Aggiorna statistiche
                    self.system_stats['messages_received'] += 1
                    self.system_stats['last_instruction_time'] = datetime.now()
                    
                else:
                    logger.warning("⚠️ Nessuna istruzione trovata nel percorso")
                    
            except Exception as e:
                logger.error(f"Errore mostrando prima istruzione: {e}")
                
                # Fallback: torna alla schermata idle in caso di errore
                try:
                    logger.warning("Tentativo di tornare alla schermata idle dopo errore prima istruzione")
                    self.display_controller.clear_display()
                    time.sleep(0.3)
                    # self.display_controller.show_idle_screen()
                except Exception as fallback_error:
                    logger.error(f"Errore anche nel fallback idle: {fallback_error}")
        
        # Avvia il thread per mostrare la prima istruzione
        instruction_thread = threading.Thread(target=show_first_instruction, daemon=True)
        instruction_thread.start()
    
    def _handle_system_commands(self, topic: str, data: Dict[str, Any]):
        """Gestisce comandi sistema"""
        try:
            command = data.get('command', '')
            logger.info(f"⚙️  Comando ricevuto: {command}")
            
            if command == 'restart':
                logger.info("🔄 Riavvio sistema richiesto...")
                self._restart_system()
                
            elif command == 'shutdown':
                logger.info("⏹️  Spegnimento sistema richiesto...")
                self._shutdown_system()
                
            elif command == 'status':
                self._publish_system_status()
                
            elif command == 'clear_display':
                self.display_controller.clear_display()
                logger.info("🧹 Display pulito")
                
            elif command == 'reset_display':
                self.display_controller.reset_display()
                logger.info("🔄 Display resettato")
                
            elif command == 'test_display':
                self._test_display()
                
            elif command == 'update_fonts':
                logger.info("📝 Aggiornamento font richiesto via MQTT")
                if self.update_font_sizes():
                    self._safe_publish_status(
                        f"micronav/pwa/{self.config['mqtt']['device_id']}/actions",
                        "font_updated",
                        "Font aggiornati con successo",
                        {}
                    )
                else:
                    self._safe_publish_status(
                        f"micronav/pwa/{self.config['mqtt']['device_id']}/actions",
                        "font_update_failed",
                        "Errore aggiornamento font",
                        {}
                    )
                
            elif command == 'set_brightness':
                brightness = data.get('brightness', 100)
                
                if self.display_controller and self.display_controller.is_initialized:
                    self.display_controller.set_brightness(brightness)
                    logger.info(f"💡 Luminosità impostata: {brightness}%")
                    
                    # Pubblica conferma
                    self._safe_publish_status(
                        f"micronav/pwa/{self.config['mqtt']['device_id']}/actions",
                        "brightness_set",
                        f"Luminosità impostata a {brightness}%",
                        {"brightness": brightness}
                    )                
                
            else:
                logger.warning(f"Comando sconosciuto: {command}")
            
        except Exception as e:
            logger.error(f"Errore gestione comando: {e}")
            self.system_stats['errors_count'] += 1
    
    def _handle_gps_position(self, topic: str, data: Dict[str, Any]):
        """Gestisce posizione GPS"""
        try:
            lat = data.get('latitude', 0)
            lon = data.get('longitude', 0)
            accuracy = data.get('accuracy', 0)
            
            logger.debug(f"📍 GPS: {lat:.6f}, {lon:.6f} (±{accuracy}m)")
            
            # Aggiorna statistiche
            self.system_stats['messages_received'] += 1
            
        except Exception as e:
            logger.error(f"Errore gestione GPS: {e}")
            self.system_stats['errors_count'] += 1
    
    def _publish_system_status(self):
        """Pubblica status dettagliato del sistema"""
        try:
            uptime = time.time() - self.start_time if self.start_time else 0
            
            # Converte datetime in stringhe per serializzazione JSON
            stats_copy = self.system_stats.copy()
            if stats_copy['last_route_time']:
                stats_copy['last_route_time'] = stats_copy['last_route_time'].isoformat()
            if stats_copy['last_instruction_time']:
                stats_copy['last_instruction_time'] = stats_copy['last_instruction_time'].isoformat()
            
            status_data = {
                'system': {
                    'uptime': uptime,
                    'version': '1.0.0',
                    'status': 'running' if self.is_running else 'stopped'
                },
                'components': {
                    'mqtt': self.system_stats['mqtt_connected'],
                    'wifi': self.system_stats['wifi_connected'],
                    'display': self.system_stats['display_active']
                },
                'statistics': stats_copy,
                'timestamp': int(time.time())
            }
            
            self._safe_publish_status(
                f"micronav/pwa/{self.config['mqtt']['device_id']}/actions",
                "online",
                "Status sistema richiesto",
                status_data
            )
            
            logger.info("📊 Status sistema pubblicato")
            
        except Exception as e:
            logger.error(f"Errore pubblicazione status: {e}")
    
    def _test_display(self):
        """Test del display"""
        try:
            logger.info("🧪 Avvio test display...")
            
            # Test istruzione
            test_instruction = {
                'instruction': 'Test display MicroNav',
                'distance': 100,
                'duration': 30,
                'maneuver': {'type': 'turn', 'modifier': 'right'},
                'icon': 'turn_right'
            }
            self.display_controller.show_navigation_instruction(test_instruction)
            
            time.sleep(3)
            
            # Test panoramica
            test_route = {
                'origin': 'Via Test, 123',
                'destination': 'Piazza Test',
                'totalDistance': 1500,
                'totalDuration': 180,
                'steps': [{'instruction': 'Test 1'}, {'instruction': 'Test 2'}]
            }
            self.display_controller.show_route_overview(test_route)
            
            time.sleep(3)
            
            # Torna a idle
            self.display_controller.show_idle_screen()
            
            logger.info("✅ Test display completato")
            
        except Exception as e:
            logger.error(f"Errore test display: {e}")
    
    def _restart_system(self):
        """Riavvia il sistema"""
        try:
            logger.info("🔄 Riavvio sistema...")
            self.stop()
            time.sleep(2)
            sys.exit(0)  # systemd riavvierà il servizio
            
        except Exception as e:
            logger.error(f"Errore riavvio: {e}")
    
    def _shutdown_system(self):
        """Spegne il sistema"""
        try:
            logger.info("⏹️  Spegnimento sistema...")
            self.stop()
            time.sleep(2)
            sys.exit(0)
            
        except Exception as e:
            logger.error(f"Errore spegnimento: {e}")
    
    def _monitor_system(self):
        """Thread di monitoraggio sistema"""
        while self.is_running:
            try:
                # Aggiorna uptime
                if self.start_time:
                    self.system_stats['uptime'] = time.time() - self.start_time
                
                # Verifica connessioni
                if self.mqtt_client:
                    self.system_stats['mqtt_connected'] = self.mqtt_client.is_connected
                
                # Verifica GPS
                if self.gps_controller:
                    self.system_stats['gps_connected'] = self.gps_controller.is_connected()
                    self.system_stats['gps_fix'] = self.gps_controller.has_fix()
                
                # WiFi monitor disabilitato
                self.system_stats['wifi_connected'] = True  # Assume sempre connesso
                
                # Verifica display
                if self.display_controller:
                    self.system_stats['display_active'] = self.display_controller.is_initialized
                
                # Aggiorna statistiche MQTT
                self._update_mqtt_system_stats()
                
                # Pubblica heartbeat ogni 60 secondi
                if (not self.last_heartbeat or 
                    time.time() - self.last_heartbeat > 60):
                    self._publish_heartbeat()
                    self.last_heartbeat = time.time()
                
                time.sleep(5)  # Check ogni 5 secondi
                
            except Exception as e:
                logger.error(f"Errore monitoraggio: {e}")
                time.sleep(10)
    
    def _publish_heartbeat(self):
        """Pubblica heartbeat del sistema"""
        try:
            if self.mqtt_client and self.mqtt_client.is_connected:
                self._safe_publish_status(
                    self.mqtt_client.topics['publish']['status'],
                    "online",
                    "Sistema attivo",
                    {
                        'uptime': self.system_stats['uptime'],
                        'messages_received': self.system_stats['messages_received'],
                        'errors_count': self.system_stats['errors_count'],
                        'gps_connected': self.system_stats.get('gps_connected', False),
                        'gps_fix': self.system_stats.get('gps_fix', False),
                        'current_position': {
                            'latitude': self.current_position.latitude if self.current_position else None,
                            'longitude': self.current_position.longitude if self.current_position else None,
                            'satellites': self.current_position.satellites if self.current_position else 0
                        } if self.current_position else None
                    }
                )
                
        except Exception as e:
            logger.error(f"Errore heartbeat: {e}")
    
    def _signal_handler(self, signum, frame):
        """Gestisce segnali di sistema"""
        logger.info(f"📡 Segnale ricevuto: {signum}")
        self.stop()
        sys.exit(0)
    
    def start(self):
        """Avvia il sistema MicroNav"""
        if self.is_running:
            logger.warning("⚠️  Sistema già in esecuzione, salto avvio")
            return True
            
        try:
            logger.info("🚀 Avvio sistema MicroNav...")
            
            # Inizializza componenti
            if not self.initialize():
                logger.error("❌ Errore inizializzazione sistema")
                return False
            
            # Avvia thread di monitoraggio
            self.is_running = True
            self.start_time = time.time()
            
            self.monitor_thread = threading.Thread(
                target=self._monitor_system,
                daemon=True
            )
            self.monitor_thread.start()
            
            # Mostra schermata idle
            self.display_controller.show_idle_screen()
            
            logger.info("🎉 Sistema MicroNav avviato con successo!")
            return True
            
        except Exception as e:
            logger.error(f"❌ Errore avvio sistema: {e}")
            return False
    
    def stop(self):
        """Ferma il sistema MicroNav"""
        try:
            logger.info("⏹️  Arresto sistema MicroNav...")
            
            self.is_running = False
            
            # Ferma componenti
            if self.mqtt_client:
                self.mqtt_client.stop()
                logger.info("✅ MQTT client fermato")
            
            # if self.wifi_monitor:
            #     self.wifi_monitor.stop()
            #     logger.info("✅ WiFi monitor fermato")
            
            if self.gps_controller:
                self.gps_controller.disconnect()
                logger.info("✅ GPS controller fermato")
            
            if self.display_controller:
                self.display_controller.stop()
                logger.info("✅ Display controller fermato")
            
            # Attendi thread
            if self.monitor_thread and self.monitor_thread.is_alive():
                self.monitor_thread.join(timeout=5)
            
            logger.info("✅ Sistema MicroNav fermato")
            
        except Exception as e:
            logger.error(f"Errore arresto sistema: {e}")
    
    def update_font_sizes(self):
        """Aggiorna le dimensioni dei font del display"""
        if self.display_controller:
            logger.info("📝 Aggiornamento dimensioni font richiesto")
            return self.display_controller.update_font_sizes()
        else:
            logger.warning("⚠️ Display controller non disponibile")
            return False
    
    def run(self):
        """Loop principale del sistema"""
        try:
            if not self.start():
                return False
            
            # Loop principale
            while self.is_running:
                try:
                    time.sleep(1)
                    
                    # Verifica stato sistema
                    if not self.system_stats['mqtt_connected']:
                        logger.warning("⚠️  MQTT disconnesso")
                        # Aggiorna indicatore MQTT sul display
                        if self.display_controller:
                            self.display_controller.update_mqtt_status(False)
                        # NON richiamare show_idle_screen qui per evitare loop

                    # Tenta riconnessione MQTT se disconnesso
                    if not self.system_stats['mqtt_connected'] and self.mqtt_client:
                        logger.info("🔄 Tentativo riconnessione MQTT...")
                        if self.mqtt_client.connect():
                            logger.info("✅ MQTT riconnesso con successo")
                            self.system_stats['mqtt_connected'] = True
                            # Aggiorna indicatore MQTT sul display
                            if self.display_controller:
                                self.display_controller.update_mqtt_status(True)
                        else:
                            logger.error("❌ Riconnessione MQTT fallita")
                    
                    # if not self.system_stats['wifi_connected']:
                    #     logger.warning("⚠️  WiFi disconnesso")
                    
                    if not self.system_stats['display_active']:
                        logger.warning("⚠️  Display non attivo")
                        # NON richiamare show_idle_screen qui per evitare loop
                        
                except Exception as e:
                    logger.error(f"Errore nel loop principale: {e}")
                    time.sleep(5)  # Attendi prima di riprovare
            
            return True
            
        except KeyboardInterrupt:
            logger.info("📡 Interruzione da tastiera")
            return True
            
        except Exception as e:
            logger.error(f"❌ Errore loop principale: {e}")
            return False
            
        finally:
            self.stop()

def main():
    """Funzione principale"""
    try:
        # Crea sistema
        system = MicroNavSystem()
        
        # Avvia sistema
        success = system.run()
        
        if success:
            logger.info("✅ Sistema MicroNav terminato correttamente")
            sys.exit(0)
        else:
            logger.error("❌ Sistema MicroNav terminato con errori")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"❌ Errore critico: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

