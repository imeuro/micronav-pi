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
# from wifi_monitor import MicroNavWiFiMonitor

# Configurazione logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/micronav/micronav-assets/logs/micronav.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class MicroNavSystem:
    """Sistema principale MicroNav Raspberry Pi"""
    
    def __init__(self):
        """Inizializza il sistema MicroNav"""
        self.config = None
        self.mqtt_client = None
        self.display_controller = None
        self.wifi_monitor = None
        
        # Stato sistema
        self.is_running = False
        self.start_time = None
        self.last_heartbeat = None
        self.current_route = None
        self.system_stats = {
            'uptime': 0,
            'mqtt_connected': False,
            'wifi_connected': False,
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
        
        logger.info("Sistema MicroNav inizializzato")
    
    def initialize(self) -> bool:
        """Inizializza tutti i componenti del sistema in parallelo"""
        try:
            logger.info("🚀 Avvio inizializzazione sistema MicroNav...")
            
            # Carica configurazione
            self.config = get_config()
            if not validate_config():
                logger.error("❌ Configurazione non valida")
                return False
            
            logger.info("✅ Configurazione caricata")
            
            # Inizializza display controller PRIMA (priorità massima)
            logger.info("📱 Inizializzazione display (priorità alta)...")
            self.display_controller = MicroNavDisplayController()
            if not self.display_controller.start():
                logger.error("❌ Errore inizializzazione display")
                return False
            
            logger.info("✅ Display inizializzato")
            
            # WiFi monitor disabilitato
            logger.info("📶 WiFi monitor disabilitato")
            self.wifi_monitor = None
            
            # Inizializza MQTT client in parallelo
            logger.info("📡 Inizializzazione MQTT client in parallelo...")
            self.mqtt_client = MicroNavMQTTClient(self.config['mqtt'])
            
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
            else:
                logger.error("❌ Errore inizializzazione MQTT client in background")
                self.system_stats['mqtt_connected'] = False
        except Exception as e:
            logger.error(f"❌ Errore MQTT asincrono: {e}")
            self.system_stats['mqtt_connected'] = False
    
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
                "gps/position",
                self._handle_gps_position
            )
            
            logger.info("✅ Handler MQTT registrati")
            
        except Exception as e:
            logger.error(f"Errore registrazione handler MQTT: {e}")
    
    # def _register_wifi_callbacks(self):
    #     """Registra callback per eventi WiFi"""
    #     try:
    #         if self.wifi_monitor:
    #             # Callback per connessione WiFi
    #             self.wifi_monitor.add_connection_callback(self._on_wifi_connected)
    #             
    #             # Callback per disconnessione WiFi
    #             self.wifi_monitor.add_disconnection_callback(self._on_wifi_disconnected)
    #             
    #             logger.info("✅ Callback WiFi registrati")
    #             
    #     except Exception as e:
    #         logger.error(f"Errore registrazione callback WiFi: {e}")
    # 
    # def _on_wifi_connected(self, network_name: str, network_info: Dict[str, Any]):
    #     """Callback per connessione WiFi"""
    #     try:
    #         ssid = network_info.get('ssid', 'Unknown')
    #         has_internet = network_info.get('has_internet', False)
    #         
    #         logger.info(f"📶 WiFi connesso: {ssid} ({network_name}) - Internet: {'✅' if has_internet else '❌'}")
    #         
    #         # Aggiorna stato sistema
    #         self.system_stats['wifi_connected'] = True
    #         
    #         # Pubblica status WiFi
    #         if self.mqtt_client and self.mqtt_client.is_connected:
    #             self.mqtt_client.publish_status(
    #                 "wifi_connected",
    #                 f"Connesso a {ssid}",
    #                 {
    #                     'network_name': network_name,
    #                     'ssid': ssid,
    #                     'has_internet': has_internet
    #                 }
    #             )
    #         
    #     except Exception as e:
    #         logger.error(f"Errore callback WiFi connesso: {e}")
    # 
    # def _on_wifi_disconnected(self, network_name: str):
    #     """Callback per disconnessione WiFi"""
    #     try:
    #         logger.warning(f"📴 WiFi disconnesso: {network_name}")
    #         
    #         # Aggiorna stato sistema
    #         self.system_stats['wifi_connected'] = False
    #         
    #         # Pubblica status WiFi
    #         if self.mqtt_client and self.mqtt_client.is_connected:
    #             self.mqtt_client.publish_status(
    #                 "wifi_disconnected",
    #                 f"Disconnesso da {network_name}",
    #                 {'network_name': network_name}
    #             )
    #         
    #     except Exception as e:
    #         logger.error(f"Errore callback WiFi disconnesso: {e}")
    
    def _handle_route_data(self, topic: str, data: Dict[str, Any]):
        """Gestisce dati percorso completo"""
        try:
            logger.info(f"📍 Percorso ricevuto: {data.get('origin', 'N/A')} → {data.get('destination', 'N/A')}")
            
            # Pulisci il display prima di mostrare il nuovo percorso
            self.display_controller.clear_display()
            time.sleep(0.5)

            # Mostra panoramica percorso sul display
            self.display_controller.show_route_overview(data)
            
            # Salva il percorso per riferimento
            self.current_route = data
            
            # Aggiorna statistiche
            self.system_stats['messages_received'] += 1
            self.system_stats['last_route_time'] = datetime.now()
            
            # Pubblica conferma ricezione
            self.mqtt_client.publish_status(
                "navigating",
                "Percorso ricevuto e visualizzato",
                {"route_id": data.get('id', 'unknown')}
            )
            
            # Mostra automaticamente la prima istruzione dopo 3 secondi
            self._show_first_instruction_after_delay(data)
            
        except Exception as e:
            logger.error(f"Errore gestione percorso: {e}")
            self.system_stats['errors_count'] += 1
    
    def _handle_navigation_step(self, topic: str, data: Dict[str, Any]):
        """Gestisce istruzione di navigazione"""
        try:
            instruction = data.get('instruction', '')
            logger.info(f"🧭 Istruzione: {instruction[:50]}...")
            
            # Mostra istruzione sul display
            self.display_controller.show_navigation_instruction(data)
            
            # Aggiorna statistiche
            self.system_stats['messages_received'] += 1
            self.system_stats['last_instruction_time'] = datetime.now()
            
            # Pubblica conferma visualizzazione
            self.mqtt_client.publish_status(
                "navigating",
                "Istruzione visualizzata",
                {"instruction_id": data.get('id', 'unknown')}
            )
            
        except Exception as e:
            logger.error(f"Errore gestione istruzione: {e}")
            self.system_stats['errors_count'] += 1
    
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
                
            elif command == 'test_display':
                self._test_display()
                
            elif command == 'set_brightness':
                brightness = data.get('brightness', 100)
                self.display_controller.set_brightness(brightness)
                logger.info(f"💡 Luminosità impostata: {brightness}%")
                
            # elif command == 'wifi_scan':
            #     if self.wifi_monitor:
            #         networks = self.wifi_monitor.debug_network_scan()
            #         logger.info(f"📡 Scansione WiFi completata: {len(networks)} reti trovate")
            #     else:
            #         logger.warning("⚠️ WiFi monitor non disponibile")
            # 
            # elif command == 'wifi_reconnect':
            #     if self.wifi_monitor:
            #         success = self.wifi_monitor.force_reconnect()
            #         logger.info(f"🔄 Riconnessione WiFi: {'✅' if success else '❌'}")
            #     else:
            #         logger.warning("⚠️ WiFi monitor non disponibile")
                
                
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
                'statistics': self.system_stats.copy(),
                'timestamp': int(time.time())
            }
            
            self.mqtt_client.publish_status(
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
                
                # WiFi monitor disabilitato
                self.system_stats['wifi_connected'] = True  # Assume sempre connesso
                
                # Verifica display
                if self.display_controller:
                    self.system_stats['display_active'] = self.display_controller.is_initialized
                
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
                self.mqtt_client.publish_status(
                    "online",
                    "Sistema attivo",
                    {
                        'uptime': self.system_stats['uptime'],
                        'messages_received': self.system_stats['messages_received'],
                        'errors_count': self.system_stats['errors_count']
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
            
            if self.display_controller:
                self.display_controller.stop()
                logger.info("✅ Display controller fermato")
            
            # Attendi thread
            if self.monitor_thread and self.monitor_thread.is_alive():
                self.monitor_thread.join(timeout=5)
            
            logger.info("✅ Sistema MicroNav fermato")
            
        except Exception as e:
            logger.error(f"Errore arresto sistema: {e}")
    
    def run(self):
        """Loop principale del sistema"""
        try:
            if not self.start():
                return False
            
            # Loop principale
            while self.is_running:
                time.sleep(1)
                
                # Verifica stato sistema
                if not self.system_stats['mqtt_connected']:
                    logger.warning("⚠️  MQTT disconnesso")
                
                # if not self.system_stats['wifi_connected']:
                #     logger.warning("⚠️  WiFi disconnesso")
                
                if not self.system_stats['display_active']:
                    logger.warning("⚠️  Display non attivo")
            
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

