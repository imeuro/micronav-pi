#!/usr/bin/env python3
"""
Client MQTT per MicroNav Raspberry Pi
Gestisce la comunicazione con il broker MQTT per ricevere dati di navigazione
"""

import json
import time
import logging
import threading
import socket
from typing import Dict, Callable, Optional, Any
from datetime import datetime

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("❌ Libreria paho-mqtt non trovata. Installa con: pip install paho-mqtt")
    exit(1)

from config import get_topics_config

# Configurazione logging
logger = logging.getLogger(__name__)

class MicroNavMQTTClient:
    """Client MQTT per MicroNav"""
    
    # Variabile di classe per tracciare le istanze
    _instances = []
    
    def __init__(self, config: Dict[str, Any]):
        """
        Inizializza il client MQTT
        
        Args:
            config: Configurazione MQTT
        """
        # Traccia le chiamate al costruttore
        import traceback
        stack = traceback.format_stack()
        logger.warning(f"🔍 Costruttore MQTT chiamato da: {stack[-2].strip()}")
        
        # Controlla se esiste già un'istanza
        if len(self._instances) > 0:
            logger.error(f"❌ Tentativo di creare istanza MQTT multipla! Esistono già {len(self._instances)} istanze")
            logger.error(f"❌ Istanza esistente: {self._instances[0]}")
            # Non creare una nuova istanza, usa quella esistente
            existing = self._instances[0]
            self.__dict__.update(existing.__dict__)
            return
        
        self.config = config
        self.client = None
        self.is_connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 5  # secondi
        
        # Carica configurazione topic
        device_id = config.get('device_id', 'vehicle-001')
        self.topics = get_topics_config(device_id)
        
        # Statistiche sistema (aggiornate dal sistema principale)
        self.system_stats = {
            'gps_fix': False,
            'gps_connected': False,
            'wifi_connected': False,
            'mqtt_connected': False
        }
        
        # Callback per gestire i messaggi ricevuti
        self.message_handlers = {}
        
        # Thread per gestire la connessione
        self.connection_thread = None
        self.running = False
        
        # Statistiche
        self.stats = {
            'messages_received': 0,
            'messages_sent': 0,
            'connection_attempts': 0,
            'last_message_time': None,
            'last_connection_time': None
        }
        
        # Aggiungi questa istanza alla lista
        self._instances.append(self)
        
        logger.info(f"Client MQTT MicroNav inizializzato (ID: {id(self)}) - Istanza {len(self._instances)}")
    
    def update_system_stats(self, stats: Dict[str, Any]):
        """Aggiorna le statistiche del sistema"""
        self.system_stats.update(stats)
    
    @classmethod
    def get_instance(cls):
        """Restituisce l'istanza singleton se esiste"""
        if len(cls._instances) > 0:
            return cls._instances[0]
        return None
    
    @classmethod
    def clear_instances(cls):
        """Pulisce tutte le istanze (per test)"""
        cls._instances.clear()
    
    def setup_client(self):
        """Configura il client MQTT"""
        try:
            # Crea client MQTT
            client_id = f"micronav_raspberry_{int(time.time())}"
            self.client = mqtt.Client(client_id=client_id)
            
            # Configura autenticazione se disponibile
            if self.config.get('username') and self.config.get('password'):
                self.client.username_pw_set(
                    self.config['username'], 
                    self.config['password']
                )
                logger.info("Autenticazione MQTT configurata")
            
            # Configura callback
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message
            self.client.on_log = self._on_log
            
            # Configura Last Will Testament
            will_topic = self.topics['publish']['status']
            will_payload = json.dumps({
                'status': 'offline',
                'timestamp': int(time.time()),
                'message': 'Raspberry Pi disconnesso',
                'ip': {
                    'ip': 'N/A',
                    'timestamp': int(time.time())
                }
            })

            self.client.will_set(will_topic, will_payload, qos=1, retain=True)


            logger.info("Client MQTT configurato")
            return True
            
        except Exception as e:
            logger.error(f"Errore configurazione client MQTT: {e}")
            return False
    
    def connect(self):
        """Connette al broker MQTT"""
        if not self.client:
            if not self.setup_client():
                return False
        
        try:
            broker_host = self.config.get('broker_host', 'localhost')
            broker_port = self.config.get('broker_port', 1883)
            keepalive = self.config.get('keepalive', 60)
            
            logger.info(f"Connessione a broker MQTT: {broker_host}:{broker_port}")
            
            self.client.connect(broker_host, broker_port, keepalive)
            self.client.loop_start()
            
            # Attendi connessione
            timeout = 10
            start_time = time.time()
            while not self.is_connected and (time.time() - start_time) < timeout:
                time.sleep(0.1)
            
            if self.is_connected:
                logger.info("✅ Connesso al broker MQTT")
                self.stats['last_connection_time'] = datetime.now()
                return True
            else:
                logger.error("❌ Timeout connessione MQTT")
                return False
                
        except Exception as e:
            logger.error(f"Errore connessione MQTT: {e}")
            return False
    
    def disconnect(self):
        """Disconnette dal broker MQTT"""
        self.running = False
        
        if self.client and self.is_connected:
            try:
                # Invia messaggio di disconnessione
                disconnect_topic = self.topics['publish']['status']
                disconnect_payload = json.dumps({
                    'status': 'offline',
                    'timestamp': int(time.time()),
                    'message': 'Disconnessione normale'
                })
                
                self.client.publish(disconnect_topic, disconnect_payload, qos=1, retain=True)

                connections_topic = self.topics['publish']['connections']
                connections_payload = json.dumps({
                    'wifi': False,
                    'mqtt': False,
                    'gps': False,
                    'gps_has_fix': False,
                    'timestamp': int(time.time()),
                    'device_id': self.topics['device_id'],
                    'device_ip_addr': "N/A"
                })
                
                self.client.publish(connections_topic, connections_payload, qos=1, retain=True)
                
                # Disconnetti
                self.client.loop_stop()
                self.client.disconnect()
                
                logger.info("Disconnesso dal broker MQTT")
                
            except Exception as e:
                logger.error(f"Errore durante disconnessione: {e}")
        
        self.is_connected = False
    
    def subscribe_to_topics(self):
        """Sottoscrive ai topic MQTT"""
        if not self.is_connected:
            logger.error("Non connesso al broker MQTT")
            return False
        
        # Topic a cui sottoscriversi
        topics = [
            self.topics['subscribe']['route_data'],      # Percorso completo
            self.topics['subscribe']['route_step'],      # Singola istruzione
            self.topics['subscribe']['commands'],        # Comandi sistema
            self.topics['subscribe']['gps_position'],    # Posizione GPS
        ]
        
        try:
            for topic in topics:
                result = self.client.subscribe(topic, qos=1)
                if result[0] != mqtt.MQTT_ERR_SUCCESS:
                    logger.error(f"❌ Errore sottoscrizione a: {topic}")
            
            return True
            
        except Exception as e:
            logger.error(f"Errore sottoscrizione topic: {e}")
            return False
    import socket

    def show_ip_addr(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                ip_address = s.getsockname()[0]
            finally:
                s.close()
            # logger.debug(f"LAN IP Address: {ip_address}")
            return ip_address
        except Exception as e:
            logger.error(f"Errore mostra IP: {e}")
            return "N/A"



    def publish_status(self, topic: str, status: str, message: str = "", extra_data: Dict = None):
        """Pubblica status del dispositivo"""
        if not self.is_connected:
            return False

        
        payload = {
            'status': status,
            'timestamp': int(time.time()),
            'message': message,
            'device_id': self.topics['device_id'],
            'uptime': time.time() - self.stats.get('start_time', time.time()),
        }
        
        if extra_data:
            payload.update(extra_data)
        
        try:
            # Converte datetime in stringhe per serializzazione JSON
            safe_payload = self._make_json_safe(payload)
            result = self.client.publish(topic, json.dumps(safe_payload), qos=1, retain=True)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self.stats['messages_sent'] += 1
                logger.debug(f"Status pubblicato: {status}")
                return True
            else:
                logger.error(f"Errore pubblicazione status: {result.rc}")
                return False
                
        except Exception as e:
            logger.error(f"Errore pubblicazione status: {e}")
            return False

    def _publish_connections_periodically(self, interval_seconds: int = 60):
        """Pubblica periodicamente lo stato delle connessioni su topic dedicato"""
        try:
            topic = self.topics['publish'].get('connections')
        except Exception:
            topic = None
        if not topic:
            logger.warning("Topic connections non configurato")
            return
        
        logger.info(f"🔄 Avvio pubblicazione periodica connessioni ogni {interval_seconds}s su {topic}")
        
        while self.running:
            try:
                ip_addr = self.show_ip_addr()
                
                payload = json.dumps({
                    'wifi': self.system_stats.get('wifi_connected', False),
                    'mqtt': self.is_connected,
                    'gps': self.system_stats.get('gps_connected', False),
                    'gps_has_fix': self.system_stats.get('gps_fix', False),
                    'timestamp': int(time.time()),
                    'device_id': self.topics['device_id'],
                    'device_ip_addr': ip_addr
                })
                
                if self.client and self.is_connected:
                    self.client.publish(topic, payload, qos=1, retain=True)
                    logger.debug(f"📡 Pubblicato stato connessioni: {payload}")
                else:
                    logger.debug("MQTT non connesso, salto pubblicazione connessioni")
                    
            except Exception as e:
                logger.error(f"Errore pubblicazione connessioni periodico: {e}")
            time.sleep(interval_seconds)

    def _make_json_safe(self, obj):
        """Converte oggetti non serializzabili in JSON in versioni sicure"""
        if isinstance(obj, dict):
            return {key: self._make_json_safe(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._make_json_safe(item) for item in obj]
        elif isinstance(obj, datetime):
            return obj.isoformat()
        elif hasattr(obj, '__dict__'):
            # Per oggetti personalizzati, prova a convertire in dict
            try:
                return self._make_json_safe(obj.__dict__)
            except:
                return str(obj)
        else:
            return obj
    
    def register_message_handler(self, topic_pattern: str, handler: Callable):
        """
        Registra un handler per messaggi su un topic specifico
        
        Args:
            topic_pattern: Pattern del topic (es. "route/data")
            handler: Funzione da chiamare quando arriva un messaggio
        """
        self.message_handlers[topic_pattern] = handler
        logger.info(f"Handler registrato per: {topic_pattern}")
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback per connessione MQTT"""
        if rc == 0:
            self.is_connected = True
            self.reconnect_attempts = 0
            self.stats['connection_attempts'] += 1
            logger.info("✅ Connesso al broker MQTT")
            
            # Sottoscrivi ai topic
            self.subscribe_to_topics()
            
            # Pubblica status online
            self.publish_status(self.topics['publish']['status'], "online", "Raspberry Pi connesso")

            # Avvia pubblicazione periodica IP
            try:
                self.connections_thread = threading.Thread(target=self._publish_connections_periodically, args=(60,), daemon=True)
                self.connections_thread.start()
            except Exception as e:
                logger.error(f"Impossibile avviare publisher IP periodico: {e}")
            
        else:
            logger.error(f"❌ Errore connessione MQTT: {rc}")
            self.is_connected = False
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback per disconnessione MQTT"""
        self.is_connected = False
        logger.warning(f"Disconnesso dal broker MQTT: {rc}")
        
        # Tentativo di riconnessione automatica
        if self.running and self.reconnect_attempts < self.max_reconnect_attempts:
            self.reconnect_attempts += 1
            logger.info(f"Tentativo riconnessione {self.reconnect_attempts}/{self.max_reconnect_attempts}")
            time.sleep(self.reconnect_delay)
            self.connect()
    
    def _on_message(self, client, userdata, msg):
        """Callback per messaggi ricevuti"""
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            
            self.stats['messages_received'] += 1
            self.stats['last_message_time'] = datetime.now()
            
            logger.info(f"📨 Messaggio ricevuto da: {topic}")
            
            # Parsing JSON
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                logger.error(f"❌ Errore parsing JSON: {payload}")
                return
            
            # Trova handler appropriato
            base_topic = self.topics['base_topic']
            topic_relative = topic.replace(f"{base_topic}/", "")
            
            for pattern, handler in self.message_handlers.items():
                if pattern in topic_relative:
                    try:
                        handler(topic_relative, data)
                    except Exception as e:
                        logger.error(f"❌ Errore handler {pattern}: {e}")
                    break
            else:
                logger.warning(f"Nessun handler per topic: {topic_relative}")
            
        except Exception as e:
            logger.error(f"Errore gestione messaggio: {e}")
    
    def _on_log(self, client, userdata, level, buf):
        """Callback per log MQTT"""
        if level <= mqtt.MQTT_LOG_WARNING:
            logger.debug(f"MQTT: {buf}")
    
    def start(self):
        """Avvia il client MQTT"""
        self.running = True
        self.stats['start_time'] = time.time()
        
        logger.info("Avvio client MQTT...")
        
        if self.connect():
            logger.info("✅ Client MQTT avviato")
            try:
                current_ip = self.show_ip_addr()
                print(f"IP locale: {current_ip}")
            except Exception:
                pass
            return True
        else:
            logger.error("❌ Errore avvio client MQTT")
            return False
    
    def stop(self):
        """Ferma il client MQTT"""
        logger.info("Arresto client MQTT...")
        self.disconnect()
        
        # Rimuovi questa istanza dalla lista
        if self in self._instances:
            self._instances.remove(self)
            logger.info(f"🗑️  Istanza MQTT rimossa. Rimangono {len(self._instances)} istanze")
        
        logger.info("✅ Client MQTT fermato")
    
    def get_stats(self) -> Dict:
        """Restituisce statistiche del client"""
        stats = self.stats.copy()
        stats['is_connected'] = self.is_connected
        stats['reconnect_attempts'] = self.reconnect_attempts
        return stats