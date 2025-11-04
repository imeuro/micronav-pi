#!/usr/bin/env python3
"""
SpeedCams Controller per MicroNav Raspberry Pi
Gestisce la rilevazione delle speedcam confrontando la posizione GPS con il database JSON
"""

import json
import os
import logging
from typing import Dict, List, Optional, Any, Tuple
from logging_config import get_logger
from gps_controller import L76KGPSController, GPSPosition, GPSStatus, calculate_distance
from mqtt_client import MicroNavMQTTClient
from config import get_speedcam_config, get_timestamp_ms, get_mqtt_topics

# Inizializza logging
logger = get_logger(__name__)


class SpeedCamsController:
    """Controller per la rilevazione delle speedcam"""
    
    def __init__(self, gps_controller: Optional[L76KGPSController] = None, 
                 mqtt_client: Optional[MicroNavMQTTClient] = None,
                 display_controller=None):
        """
        Inizializza il controller per le speedcam
        
        Args:
            gps_controller: Controller GPS per ottenere la posizione
            mqtt_client: Client MQTT per pubblicare notifiche
            display_controller: Controller display per visualizzare alert
        """
        self.gps_controller = gps_controller
        self.mqtt_client = mqtt_client
        self.display_controller = display_controller
        
        # Configurazione
        self.config = get_speedcam_config()
        self.radius = self.config.get('detection_radius', 1000)  # Default 1km
        self.check_interval = self.config.get('check_interval', 5.0)  # Default 5 secondi
        self.enabled = self.config.get('enabled', True)
        
        # Database speedcam
        self.speedcams: List[Dict[str, Any]] = []
        self.json_path = self.config.get('json_path', '')
        
        # Stato rilevazione (anti-duplicati)
        self.last_detected_speedcam_id: Optional[int] = None
        self.last_detected_distance: Optional[float] = None
        self.is_monitoring = False
        
        # Throttling per evitare check troppo frequenti
        self.last_check_time: Optional[float] = None
        
        # Fallback: ultima posizione ricevuta via MQTT (da PWA)
        self.last_mqtt_position: Optional[Dict[str, Any]] = None
        self.last_mqtt_position_time: Optional[float] = None
        
        # Statistiche
        self.stats = {
            'speedcams_loaded': 0,
            'detections_count': 0,
            'last_detection_time': None
        }
        
        logger.info(f"SpeedCams Controller inizializzato - Raggio: {self.radius}m, Enabled: {self.enabled}")
        
        # Carica speedcam se abilitato
        if self.enabled:
            self.load_speedcams()
    
    def load_speedcams(self) -> bool:
        """
        Carica le speedcam dal file JSON
        
        Returns:
            bool: True se caricamento riuscito
        """
        if not self.json_path:
            logger.error("❌ Path JSON speedcam non configurato")
            return False
        
        try:
            # Verifica esistenza file
            if not os.path.exists(self.json_path):
                logger.error(f"❌ File JSON speedcam non trovato: {self.json_path}")
                self.enabled = False
                return False
            
            # Carica JSON
            logger.info(f"📂 Caricamento speedcam da {self.json_path}...")
            with open(self.json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Estrai array speedcam
            if 'result' in data and isinstance(data['result'], list):
                self.speedcams = data['result']
                self.stats['speedcams_loaded'] = len(self.speedcams)
                logger.info(f"✅ Caricate {len(self.speedcams)} speedcam dal database")
                return True
            else:
                logger.error(f"❌ Formato JSON non valido: chiave 'result' non trovata o non è una lista")
                self.enabled = False
                return False
                
        except json.JSONDecodeError as e:
            logger.error(f"❌ Errore parsing JSON: {e}")
            self.enabled = False
            return False
        except Exception as e:
            logger.error(f"❌ Errore caricamento speedcam: {e}")
            self.enabled = False
            return False
    
    def check_speedcams(self, position: Optional[GPSPosition] = None) -> Optional[Dict[str, Any]]:
        """
        Verifica speedcam vicine basandosi sulla posizione GPS attuale
        
        Args:
            position: Posizione GPS da usare. Se None, viene ottenuta dal GPS controller.
                     Passare la posizione come parametro evita deadlock quando chiamato da callback GPS.
        
        Returns:
            Optional[Dict]: Dati speedcam rilevata se trovata, None altrimenti
        """
        try:
            # Throttling: esegui check solo ogni X secondi
            import time
            current_time = time.time()
            if self.last_check_time is not None:
                time_since_last_check = current_time - self.last_check_time
                if time_since_last_check < self.check_interval:
                    return None  # Troppo presto, salta il check
            
            if not self.enabled:
                return None
            
            if not self.gps_controller:
                return None
            
            # Se la posizione è stata passata come parametro, usala direttamente (evita deadlock)
            if position is None:
                # Verifica che il GPS abbia un fix valido
                if not self.gps_controller or not self.gps_controller.has_fix():
                    # Fallback: prova a usare posizione da MQTT (PWA)
                    position = self._get_position_from_mqtt()
                    if position is None:
                        return None
                else:
                    # Ottieni posizione attuale dal GPS controller
                    try:
                        position = self.gps_controller.get_position()
                    except Exception as e:
                        logger.error(f"Errore ottenimento posizione GPS: {e}")
                        # Fallback: prova a usare posizione da MQTT (PWA)
                        position = self._get_position_from_mqtt()
                        if position is None:
                            return None
            
            # Verifica validità posizione
            if not position.is_valid:
                return None
            
            # Aggiorna tempo ultimo check
            self.last_check_time = current_time
            
            # Rileva speedcam vicine
            result = self._detect_speedcam(position, self.radius)
            return result
            
        except Exception as e:
            logger.error(f"Errore in check_speedcams(): {e}")
            return None
    
    def _detect_speedcam(self, position: GPSPosition, radius: float = 1000) -> Optional[Dict[str, Any]]:
        """
        Rileva speedcam entro raggio dalla posizione GPS
        
        Args:
            position: Posizione GPS corrente
            radius: Raggio di rilevazione in metri (default 1000m)
            
        Returns:
            Optional[Dict]: Dati speedcam più vicina se trovata nel raggio, None altrimenti
        """
        if not self.speedcams:
            return None
        
        closest_speedcam = None
        closest_distance = float('inf')
        
        try:
            # Calcola distanza da tutte le speedcam e trova la più vicina
            for speedcam in self.speedcams:
                try:
                    sc_lat = speedcam.get('lat')
                    sc_lng = speedcam.get('lng')
                    
                    # Verifica che le coordinate siano valide
                    if sc_lat is None or sc_lng is None:
                        continue
                    
                    # Calcola distanza usando formula Haversine
                    distance = calculate_distance(
                        position.latitude,
                        position.longitude,
                        sc_lat,
                        sc_lng
                    )
                    
                    # Se è entro il raggio e più vicina di quella già trovata
                    if distance <= radius and distance < closest_distance:
                        closest_distance = distance
                        closest_speedcam = speedcam.copy()
                        closest_speedcam['distance'] = distance
                        
                except (KeyError, ValueError, TypeError) as e:
                    logger.debug(f"Errore calcolo distanza speedcam {speedcam.get('id', 'unknown')}: {e}")
                    continue
            
            # Se non trovata nessuna speedcam nel raggio
            if closest_speedcam is None:
                # Reset stato se si è usciti dal raggio dell'ultima speedcam
                if self.last_detected_speedcam_id is not None:
                    logger.debug(f"🚪 Uscito dal raggio di rilevamento speedcam ID {self.last_detected_speedcam_id}")
                    self.last_detected_speedcam_id = None
                    self.last_detected_distance = None
                    
                    # Nascondi l'alert sul display
                    if self.display_controller:
                        self.display_controller.current_speedcam = None
                        self.display_controller.current_speedcam_distance = None
                        # Ridisegna la schermata corrente senza alert
                        self._redraw_current_screen()
                return None
            
            # Verifica anti-duplicati e aggiornamenti distanza
            # 1. È una speedcam diversa da quella già rilevata → notifica completa
            # 2. È la stessa speedcam ma la distanza è diminuita significativamente (>50m) → notifica completa
            # 3. È la stessa speedcam e la distanza è cambiata di almeno 10m → aggiorna solo display
            speedcam_id = closest_speedcam.get('id')
            
            should_detect = False  # Notifica completa (log + MQTT + visualizza)
            should_update_distance = False  # Solo aggiornamento visivo distanza
            
            if self.last_detected_speedcam_id is None:
                # Prima rilevazione
                should_detect = True
            elif speedcam_id != self.last_detected_speedcam_id:
                # Nuova speedcam
                should_detect = True
            elif self.last_detected_distance is not None:
                # Stessa speedcam: calcola differenza distanza
                distance_diff = self.last_detected_distance - closest_distance
                
                # Notifica completa se ci si avvicina significativamente (>50m)
                if distance_diff >= 50:
                    should_detect = True
                # Aggiorna solo distanza se cambia di almeno 10m (in qualsiasi direzione)
                elif abs(distance_diff) >= 10:
                    should_update_distance = True
            
            if should_detect:
                # Aggiorna stato
                self.last_detected_speedcam_id = speedcam_id
                self.last_detected_distance = closest_distance
                
                # Notifica rilevazione completa (log + MQTT + visualizza)
                self._notify_speedcam_detected(closest_speedcam, closest_distance)
                
                return closest_speedcam
            elif should_update_distance:
                # Aggiorna solo la distanza mostrata (senza log/MQTT)
                self.last_detected_distance = closest_distance
                
                if self.display_controller:
                    # Aggiorna solo la distanza nell'alert
                    self.display_controller.current_speedcam_distance = closest_distance
                    # Ridisegna l'alert con la nuova distanza (solo aggiornamento visivo)
                    self.display_controller.show_speedcam_alert(
                        self.display_controller.current_speedcam,
                        closest_distance
                    )
                
                logger.debug(f"📏 Aggiornamento distanza speedcam ID {speedcam_id}: {closest_distance:.0f}m")
                return closest_speedcam
            
            return None
            
        except Exception as e:
            logger.error(f"Errore rilevazione speedcam: {e}")
            return None
    
    def _notify_speedcam_detected(self, speedcam: Dict[str, Any], distance: float):
        """
        Gestisce le notifiche quando viene rilevata una speedcam
        
        Args:
            speedcam: Dati speedcam rilevata
            distance: Distanza dalla speedcam in metri
        """
        try:
            speedcam_id = speedcam.get('id', 'unknown')
            speedcam_type = speedcam.get('type', 'unknown')
            speedcam_vmax = speedcam.get('vmax', '?')
            speedcam_strasse = speedcam.get('strasse', '')
            speedcam_ort = speedcam.get('ort', '')
            
            # Log
            location_str = f"{speedcam_strasse}, {speedcam_ort}" if speedcam_strasse or speedcam_ort else "Posizione sconosciuta"
            logger.warning(
                f"🚨 Speedcam rilevata - ID: {speedcam_id}, "
                f"Tipo: {speedcam_type}, Limite: {speedcam_vmax} km/h, "
                f"Distanza: {distance:.0f}m, Posizione: {location_str}"
            )
            
            # Aggiorna statistiche
            self.stats['detections_count'] += 1
            from datetime import datetime
            self.stats['last_detection_time'] = datetime.now()
            
            # Pubblicazione MQTT
            if self.mqtt_client:
                self._publish_mqtt_notification(speedcam, distance)
            
            # Visualizzazione sul display
            if self.display_controller:
                self._show_display_alert(speedcam, distance)
                
        except Exception as e:
            logger.error(f"Errore notifica rilevazione speedcam: {e}")
    
    def _publish_mqtt_notification(self, speedcam: Dict[str, Any], distance: float):
        """
        Pubblica notifica MQTT per speedcam rilevata
        
        Args:
            speedcam: Dati speedcam
            distance: Distanza dalla speedcam
        """
        try:
            if not self.mqtt_client or not hasattr(self.mqtt_client, 'is_connected'):
                return
            
            if not self.mqtt_client.is_connected:
                logger.info("MQTT non connesso, salto pubblicazione notifica speedcam")
                return
            
            # Ottieni topic MQTT
            topics = get_mqtt_topics()
            topic = topics['publish'].get('speedcam_detected')
            
            if not topic:
                logger.warning("Topic MQTT speedcam non configurato")
                return
            
            # Prepara payload
            payload = {
                'status': 'detected',
                'timestamp': get_timestamp_ms(),
                'speedcam': {
                    'id': speedcam.get('id'),
                    'lat': speedcam.get('lat'),
                    'lng': speedcam.get('lng'),
                    'type': speedcam.get('type'),
                    'vmax': speedcam.get('vmax'),
                    'art': speedcam.get('art'),
                    'distance': round(distance, 1),
                    'strasse': speedcam.get('strasse', ''),
                    'ort': speedcam.get('ort', ''),
                    'landkreis': speedcam.get('landkreis', '')
                }
            }
            
            # Pubblica
            self.mqtt_client.client.publish(topic, json.dumps(payload), qos=1, retain=False)
            logger.debug(f"📡 Notifica speedcam pubblicata su MQTT: {topic}")
            
        except Exception as e:
            logger.error(f"Errore pubblicazione MQTT speedcam: {e}")
    
    def _show_display_alert(self, speedcam: Dict[str, Any], distance: float):
        """
        Mostra alert sul display per speedcam rilevata
        
        Args:
            speedcam: Dati speedcam
            distance: Distanza dalla speedcam
        """
        try:
            if not self.display_controller:
                return
            
            # Chiama metodo display controller se disponibile
            if hasattr(self.display_controller, 'show_speedcam_alert'):
                self.display_controller.show_speedcam_alert(speedcam, distance)
            else:
                logger.debug("Display controller non supporta show_speedcam_alert")
                
        except Exception as e:
            logger.error(f"Errore visualizzazione alert display: {e}")
    
    def _redraw_current_screen(self):
        """
        Ridisegna la schermata corrente senza l'alert speedcam
        (chiamato quando si esce dal raggio di rilevamento)
        """
        try:
            if not self.display_controller:
                return
            
            # Ottieni la schermata corrente dal display controller
            current_screen = self.display_controller.display_state.get('current_screen', 'idle')
            
            # Ridisegna la schermata appropriata (senza alert, già impostato a None)
            if current_screen == 'idle':
                if hasattr(self.display_controller, 'show_idle_screen'):
                    self.display_controller.show_idle_screen()
            elif current_screen == 'navigation':
                # Per la navigazione, ridisegna con l'ultima istruzione se disponibile
                if hasattr(self.display_controller, 'show_navigation_instruction'):
                    last_instruction = getattr(self.display_controller, 'current_instruction', None)
                    if last_instruction:
                        self.display_controller.show_navigation_instruction(last_instruction)
                    else:
                        # Se non c'è istruzione salvata, fallback a idle screen
                        if hasattr(self.display_controller, 'show_idle_screen'):
                            self.display_controller.show_idle_screen()
            elif current_screen == 'route_overview':
                # Per la panoramica, ridisegna con l'ultimo percorso se disponibile
                if hasattr(self.display_controller, 'show_route_overview'):
                    last_route = getattr(self.display_controller, 'current_route', None)
                    if last_route:
                        self.display_controller.show_route_overview(last_route)
                    else:
                        # Se non c'è percorso salvato, fallback a idle screen
                        if hasattr(self.display_controller, 'show_idle_screen'):
                            self.display_controller.show_idle_screen()
            
            logger.debug(f"🔄 Schermata {current_screen} ridisegnata senza alert speedcam")
                
        except Exception as e:
            logger.error(f"Errore ridisegno schermata corrente: {e}")
    
    def start(self):
        """Avvia il monitoraggio delle speedcam"""
        if not self.enabled:
            logger.warning("Speedcam controller disabilitato, non avvio monitoraggio")
            return
        
        if self.is_monitoring:
            logger.info("Monitoraggio speedcam già attivo")
            return
        
        self.is_monitoring = True
        
        logger.info("🚀 Monitoraggio speedcam avviato")
    
    def stop(self):
        """Ferma il monitoraggio delle speedcam"""
        self.is_monitoring = False
        logger.info("⏹️  Monitoraggio speedcam fermato")
    
    def _check_mqtt_position_message(self, topic: str, data: Dict[str, Any]):
        """
        Controlla se un messaggio MQTT è una posizione PWA e la gestisce
        
        Args:
            topic: Topic del messaggio
            data: Dati del messaggio
        """
        try:
            from config import get_mqtt_topics
            topics = get_mqtt_topics()
            expected_topic = topics['subscribe'].get('pwa_position')
            
            if expected_topic and (topic == expected_topic or "micronav/pwa" in topic):
                self._handle_mqtt_position(topic, data)
        except Exception as e:
            logger.debug(f"Errore controllo messaggio MQTT position: {e}")
    
    def _handle_mqtt_position(self, topic: str, data: Dict[str, Any]):
        """
        Handler per messaggi MQTT di posizione da PWA
        
        Args:
            topic: Topic del messaggio
            data: Dati del messaggio
        """
        try:
            # Verifica che i dati contengano latitude e longitude
            if 'latitude' in data and 'longitude' in data:
                import time
                self.last_mqtt_position = data
                self.last_mqtt_position_time = time.time()
                logger.debug(f"📍 Posizione ricevuta via MQTT (PWA): {data.get('latitude')}, {data.get('longitude')}")
        except Exception as e:
            logger.debug(f"Errore gestione posizione MQTT: {e}")
    
    def _get_position_from_mqtt(self) -> Optional[GPSPosition]:
        """
        Ottiene la posizione dall'ultimo messaggio MQTT ricevuto
        
        Returns:
            Optional[GPSPosition]: Posizione GPS o None se non disponibile
        """
        try:
            import time
            
            # Verifica che abbiamo una posizione MQTT recente (max 15 minuti)
            if self.last_mqtt_position is None or self.last_mqtt_position_time is None:
                return None
            
            # Verifica che la posizione non sia troppo vecchia
            time_since_update = time.time() - self.last_mqtt_position_time
            if time_since_update > 15*60:  # Max 15 minuti
                logger.debug(f"Posizione MQTT troppo vecchia ({time_since_update:.0f}s), ignorata")
                return None
            
            # Estrai coordinate
            lat = self.last_mqtt_position.get('latitude')
            lng = self.last_mqtt_position.get('longitude')
            
            if lat is None or lng is None:
                return None
            
            # Crea oggetto GPSPosition
            position = GPSPosition(
                latitude=float(lat),
                longitude=float(lng),
                altitude=self.last_mqtt_position.get('altitude', 0.0),
                speed=self.last_mqtt_position.get('speed', 0.0),
                course=self.last_mqtt_position.get('course', 0.0),
                satellites=self.last_mqtt_position.get('satellites', 0),
                hdop=self.last_mqtt_position.get('hdop', 0.0),
                fix_quality=self.last_mqtt_position.get('fix_quality', 0),
                is_valid=True,
                timestamp=None
            )
            
            logger.debug(f"📍 Usando posizione MQTT come fallback: {lat}, {lng}")
            return position
            
        except Exception as e:
            logger.debug(f"Errore ottenimento posizione da MQTT: {e}")
            return None
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Ottiene le statistiche del controller
        
        Returns:
            Dict: Statistiche
        """
        return {
            'enabled': self.enabled,
            'is_monitoring': self.is_monitoring,
            'speedcams_loaded': self.stats['speedcams_loaded'],
            'detections_count': self.stats['detections_count'],
            'last_detection_time': self.stats['last_detection_time'],
            'radius': self.radius,
            'last_detected_speedcam_id': self.last_detected_speedcam_id,
            'last_detected_distance': self.last_detected_distance,
            'has_mqtt_position_fallback': self.last_mqtt_position is not None
        }
