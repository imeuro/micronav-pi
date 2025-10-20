#!/usr/bin/env python3
"""
Client MQTT per MicroNav Raspberry Pi
Gestisce la comunicazione con il broker MQTT per ricevere dati di navigazione
"""

import json
import time
import logging
import threading
from typing import Dict, Callable, Optional, Any
from datetime import datetime

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("❌ Libreria paho-mqtt non trovata. Installa con: pip install paho-mqtt")
    exit(1)

from config import get_topics_config

# Configurazione logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class MicroNavMQTTClient:
    """Client MQTT per MicroNav"""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Inizializza il client MQTT
        
        Args:
            config: Configurazione MQTT
        """
        self.config = config
        self.client = None
        self.is_connected = False
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 5  # secondi
        
        # Carica configurazione topic
        device_id = config.get('device_id', 'car01')
        self.topics = get_topics_config(device_id)
        
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
        
        logger.info("Client MQTT MicroNav inizializzato")
    
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
                'message': 'Raspberry Pi disconnesso'
            })
            
            self.client.will_set(
                will_topic, 
                will_payload, 
                qos=1, 
                retain=True
            )
            
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
                if result[0] == mqtt.MQTT_ERR_SUCCESS:
                    logger.info(f"✅ Sottoscritto a: {topic}")
                else:
                    logger.error(f"❌ Errore sottoscrizione a: {topic}")
            
            return True
            
        except Exception as e:
            logger.error(f"Errore sottoscrizione topic: {e}")
            return False
    
    def publish_status(self, status: str, message: str = "", extra_data: Dict = None):
        """Pubblica status del dispositivo"""
        if not self.is_connected:
            return False
        
        topic = self.topics['publish']['status']
        
        payload = {
            'status': status,
            'timestamp': int(time.time()),
            'message': message,
            'device_id': self.topics['device_id'],
            'uptime': time.time() - self.stats.get('start_time', time.time())
        }
        
        if extra_data:
            payload.update(extra_data)
        
        try:
            # Converte datetime in stringhe per serializzazione JSON
            safe_payload = self._make_json_safe(payload)
            result = self.client.publish(topic, json.dumps(safe_payload), qos=1, retain=True)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self.stats['messages_sent'] += 1
                logger.info(f"Status pubblicato: {status}")
                return True
            else:
                logger.error(f"Errore pubblicazione status: {result.rc}")
                return False
                
        except Exception as e:
            logger.error(f"Errore pubblicazione status: {e}")
            return False
    
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
            self.publish_status("online", "Raspberry Pi connesso")
            
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
            return True
        else:
            logger.error("❌ Errore avvio client MQTT")
            return False
    
    def stop(self):
        """Ferma il client MQTT"""
        logger.info("Arresto client MQTT...")
        self.disconnect()
        logger.info("✅ Client MQTT fermato")
    
    def get_stats(self) -> Dict:
        """Restituisce statistiche del client"""
        stats = self.stats.copy()
        stats['is_connected'] = self.is_connected
        stats['reconnect_attempts'] = self.reconnect_attempts
        return stats

# Esempio di utilizzo
if __name__ == "__main__":
    # Configurazione di esempio
    config = {
        'broker_host': 'tuoserver.it',  # Sostituisci con il tuo server
        'broker_port': 1883,
        'username': 'micronav',
        'password': 'tuapassword',
        'device_id': 'car01',
        'keepalive': 60
    }
    
    # Crea client MQTT
    mqtt_client = MicroNavMQTTClient(config)
    
    # Handler per messaggi di percorso
    def handle_route_data(topic, data):
        print(f"📍 Percorso ricevuto: {data.get('origin')} → {data.get('destination')}")
        print(f"   Distanza: {data.get('totalDistance', 0)}m")
        print(f"   Durata: {data.get('totalDuration', 0)}s")
        print(f"   Istruzioni: {len(data.get('steps', []))}")
    
    # Handler per istruzioni di navigazione
    def handle_navigation_step(topic, data):
        print(f"🧭 Istruzione: {data.get('instruction', 'N/A')}")
        print(f"   Distanza: {data.get('distance', 0)}m")
        print(f"   Durata: {data.get('duration', 0)}s")
    
    # Handler per comandi
    def handle_commands(topic, data):
        print(f"⚙️  Comando ricevuto: {data.get('command', 'N/A')}")
    
    # Registra handler
    mqtt_client.register_message_handler("route/data", handle_route_data)
    mqtt_client.register_message_handler("route/step", handle_navigation_step)
    mqtt_client.register_message_handler("commands", handle_commands)
    
    try:
        # Avvia client
        if mqtt_client.start():
            print("✅ Client MQTT avviato. Premi Ctrl+C per fermare.")
            
            # Loop principale
            while True:
                time.sleep(1)
                
                # Pubblica status ogni 30 secondi
                if int(time.time()) % 30 == 0:
                    mqtt_client.publish_status("online", "Raspberry Pi attivo")
        else:
            print("❌ Errore avvio client MQTT")
    
    except KeyboardInterrupt:
        print("\n⏹️  Arresto client MQTT...")
        mqtt_client.stop()
        print("✅ Client MQTT fermato")
    
    except Exception as e:
        print(f"❌ Errore: {e}")
        mqtt_client.stop()
