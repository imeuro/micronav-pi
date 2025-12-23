#!/usr/bin/env python3
"""
Route Manager per MicroNav Raspberry Pi
Gestisce il routing automatico: calcolo step corrente, verifica distanza percorso, rilevamento deviazioni
"""

import math
import logging
import time
import json
import urllib.request
import urllib.parse
import urllib.error
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from datetime import datetime

from gps_controller import GPSPosition, calculate_distance
from config import get_config, get_timestamp_ms

# Configurazione logging
logger = logging.getLogger(__name__)


@dataclass
class RouteStep:
    """Dati di uno step del percorso"""
    index: int
    instruction: str
    distance: float
    duration: int
    maneuver: Dict[str, Any]
    icon: str
    coordinates: Dict[str, Any]  # start, end, geometry
    bearing: Optional[float] = None


@dataclass
class RouteDeviation:
    """Dati di deviazione dal percorso"""
    distance: float  # Distanza minima dal percorso in metri
    threshold_warning: float = 50.0  # Soglia warning (metri)
    threshold_recalculate: float = 100.0  # Soglia per ricalcolo (metri)
    is_deviated: bool = False
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class RouteManager:
    """Gestore routing automatico per MicroNav"""
    
    def __init__(self, config: Dict[str, Any] = None):
        """
        Inizializza il Route Manager
        
        Args:
            config: Configurazione sistema (opzionale)
        """
        self.config = config or get_config()
        
        # Percorso corrente
        self.current_route: Optional[Dict[str, Any]] = None
        self.route_steps: List[RouteStep] = []
        self.route_geometry: List[Tuple[float, float]] = []  # Lista di (lat, lng)
        
        # Destinazione originale (per ricalcolo automatico)
        self.destination_coords: Optional[Dict[str, float]] = None  # {lat, lng}
        self.destination_address: Optional[str] = None
        
        # Step corrente
        self.current_step_index: int = -1
        self.current_step: Optional[RouteStep] = None
        self.last_step_update_time: float = 0.0
        
        # Configurazione da config
        route_manager_config = self.config.get('route_manager', {})
        self.step_update_interval = route_manager_config.get('step_update_interval', 5.0)
        self.deviation_threshold_warning = route_manager_config.get('deviation_threshold_warning', 50.0)
        self.deviation_threshold_recalculate = route_manager_config.get('deviation_threshold_recalculate', 100.0)
        
        # Deviazione
        self.deviation: Optional[RouteDeviation] = None
        
        # Callback per notifiche
        self.on_route_recalculated = None  # Callback per notificare ricalcolo completato
        
        # Configurazione Mapbox per ricalcolo
        mapbox_config = self.config.get('mapbox', {})
        self.mapbox_enabled = mapbox_config.get('enabled', True)
        self.mapbox_access_token = mapbox_config.get('access_token')
        self.mapbox_api_base_url = mapbox_config.get('api_base_url', 'https://api.mapbox.com/directions/v5')
        self.mapbox_routing_profile = mapbox_config.get('routing_profile', 'driving')
        self.mapbox_language = mapbox_config.get('language', 'it')
        self.mapbox_timeout = mapbox_config.get('timeout', 10.0)
        
        # Log stato configurazione Mapbox
        if self.mapbox_enabled:
            if self.mapbox_access_token:
                # Nascondi il token nei log (mostra solo primi 10 caratteri)
                token_preview = self.mapbox_access_token[:10] + '...' if len(self.mapbox_access_token) > 10 else '***'
                logger.info(f"✅ Mapbox configurato - Token: {token_preview}, API: {self.mapbox_api_base_url}")
            else:
                logger.warning("⚠️ Mapbox abilitato ma access_token non configurato - ricalcolo automatico non disponibile")
                logger.warning("   Verifica che MAPBOX_ACCESS_TOKEN sia presente nel file .env")
        else:
            logger.info("ℹ️ Mapbox disabilitato - ricalcolo automatico non disponibile")
        
        # Flag per evitare ricalcoli multipli simultanei
        self.recalculating = False
        self.last_recalculate_time: float = 0.0
        self.recalculate_cooldown: float = 30.0  # Secondi tra ricalcoli
        
        # Statistiche
        self.stats = {
            'step_updates': 0,
            'deviation_checks': 0,
            'warnings': 0,
            'recalculate_requests': 0,
            'recalculate_success': 0,
            'recalculate_failed': 0,
            'last_update': None
        }
        
        logger.info("Route Manager inizializzato")
    
    def set_route(self, route_data: Dict[str, Any]) -> bool:
        """
        Imposta il percorso corrente
        
        Args:
            route_data: Dati percorso completo (formato MQTT)
            
        Returns:
            bool: True se percorso impostato correttamente
        """
        try:
            if not route_data or route_data.get('type') != 'route':
                logger.warning("Dati percorso non validi")
                return False
            
            # Salva percorso
            self.current_route = route_data
            
            # Log per debug messaggio completo
            logger.info(f"Messaggio percorso ricevuto - keys: {list(route_data.keys())}")
            logger.info(f"destCoords presente: {route_data.get('destCoords') is not None}, value: {route_data.get('destCoords')}")
            logger.info(f"destination presente: {route_data.get('destination') is not None}, value: {route_data.get('destination')}")
            logger.info(f"routeGeometry presente: {route_data.get('routeGeometry') is not None}, type: {type(route_data.get('routeGeometry'))}")
            
            # Estrai coordinate percorso completo
            route_geometry = route_data.get('routeGeometry', [])
            
            # Log per debug
            logger.info(f"Route geometry presente: {route_geometry is not None}, type: {type(route_geometry)}, len: {len(route_geometry) if isinstance(route_geometry, list) else 'N/A'}")
            if isinstance(route_geometry, list) and len(route_geometry) > 0:
                logger.info(f"Route geometry primo punto: {route_geometry[0]}")
            
            if route_geometry and isinstance(route_geometry, list) and len(route_geometry) > 0:
                # Converti lista di [lat, lng] in lista di tuple (lat, lng)
                try:
                    self.route_geometry = [(float(coord[0]), float(coord[1])) 
                                          for coord in route_geometry]
                    logger.info(f"Route geometry convertito: {len(self.route_geometry)} punti")
                except Exception as e:
                    logger.error(f"Errore conversione route geometry: {e}")
                    self.route_geometry = []
            else:
                logger.warning("Percorso senza coordinate geometry valide")
                self.route_geometry = []
            
            # Estrai e processa steps
            steps_data = route_data.get('steps', [])
            self.route_steps = []
            
            steps_with_coords = 0
            steps_without_coords = 0
            
            for index, step_data in enumerate(steps_data):
                # Log per debug coordinate
                coordinates_data = step_data.get('coordinates', {})
                
                # Log dettagliato per vedere cosa arriva (solo per i primi 2 step per non intasare i log)
                if index < 2:
                    logger.info(f"Step {index} - coordinates type: {type(coordinates_data)}, keys: {list(coordinates_data.keys()) if isinstance(coordinates_data, dict) else 'N/A'}")
                    if isinstance(coordinates_data, dict):
                        logger.info(f"Step {index} - start: {coordinates_data.get('start')}, end: {coordinates_data.get('end')}, geometry len: {len(coordinates_data.get('geometry', []))}")
                    else:
                        logger.info(f"Step {index} - coordinates value: {coordinates_data}")
                
                step = RouteStep(
                    index=index,
                    instruction=step_data.get('instruction', ''),
                    distance=float(step_data.get('distance', 0)),
                    duration=int(step_data.get('duration', 0)),
                    maneuver=step_data.get('maneuver', {}),
                    icon=step_data.get('icon', ''),
                    coordinates=coordinates_data if isinstance(coordinates_data, dict) else {},
                    bearing=step_data.get('maneuver', {}).get('bearing')
                )
                self.route_steps.append(step)
                
                # Verifica se ha coordinate valide
                if isinstance(coordinates_data, dict):
                    if coordinates_data.get('start') and isinstance(coordinates_data.get('start'), dict) and coordinates_data.get('start').get('lat'):
                        steps_with_coords += 1
                    elif coordinates_data.get('end') and isinstance(coordinates_data.get('end'), dict) and coordinates_data.get('end').get('lat'):
                        steps_with_coords += 1
                        logger.debug(f"Step {index} usa coordinate end (start non disponibile)")
                    elif coordinates_data.get('geometry') and len(coordinates_data.get('geometry', [])) > 0:
                        steps_with_coords += 1
                        logger.debug(f"Step {index} usa coordinate geometry (start/end non disponibili)")
                    else:
                        steps_without_coords += 1
                        if index < 2:  # Log dettagliato solo per i primi 2
                            logger.info(f"Step {index} senza coordinate valide - start: {coordinates_data.get('start')}, end: {coordinates_data.get('end')}, geometry: {coordinates_data.get('geometry')}")
                else:
                    steps_without_coords += 1
                    if index < 2:
                        logger.warning(f"Step {index} coordinates non è un dict: {type(coordinates_data)} = {coordinates_data}")
            
            # Salva destinazione originale per ricalcolo automatico
            self.destination_coords = route_data.get('destCoords')
            self.destination_address = route_data.get('destination')
            
            if not self.destination_coords:
                logger.warning("⚠️ Destinazione non trovata nel percorso - ricalcolo automatico potrebbe non funzionare")
            
            # Reset step corrente
            self.current_step_index = -1
            self.current_step = None
            self.deviation = None
            
            logger.info(f"✅ Percorso impostato: {len(self.route_steps)} steps, "
                       f"{len(self.route_geometry)} punti percorso, "
                       f"{steps_with_coords} con coordinate, {steps_without_coords} senza coordinate")
            
            return True
            
        except Exception as e:
            logger.error(f"Errore impostazione percorso: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False
    
    def clear_route(self):
        """Rimuove il percorso corrente"""
        self.current_route = None
        self.route_steps = []
        self.route_geometry = []
        self.current_step_index = -1
        self.current_step = None
        self.deviation = None
        self.destination_coords = None
        self.destination_address = None
        logger.info("Percorso rimosso")
    
    def calculate_current_step(self, gps_position: GPSPosition) -> Optional[RouteStep]:
        """
        Calcola quale step mostrare in base alla posizione GPS
        
        Args:
            gps_position: Posizione GPS corrente
            
        Returns:
            RouteStep: Step corrente o None se non trovato
        """
        if not self.current_route or not self.route_steps:
            logger.debug(f"Route manager: nessun percorso attivo (current_route={self.current_route is not None}, steps={len(self.route_steps)})")
            return None
        
        if not gps_position.is_valid:
            logger.debug(f"Route manager: GPS non valido (is_valid={gps_position.is_valid})")
            return None
        
        try:
            current_time = time.time()
            
            # Throttling: aggiorna step solo ogni N secondi
            # MA: se non abbiamo ancora uno step corrente, calcolalo comunque
            if self.current_step is not None:
                if current_time - self.last_step_update_time < self.step_update_interval:
                    logger.debug(f"Route manager: throttling (mancano {self.step_update_interval - (current_time - self.last_step_update_time):.1f}s)")
                    return self.current_step
            
            logger.debug(f"Route manager: calcolo step corrente (GPS: {gps_position.latitude:.6f}, {gps_position.longitude:.6f})")
            logger.debug(f"Route manager: steps disponibili: {len(self.route_steps)}")
            
            # Trova lo step più vicino alla posizione GPS
            closest_step = None
            closest_distance = float('inf')
            closest_index = -1
            steps_with_coords = 0
            
            for index, step in enumerate(self.route_steps):
                # Verifica che coordinates sia un dict
                if not isinstance(step.coordinates, dict):
                    logger.debug(f"Route manager: step {index} coordinates non è un dict: {type(step.coordinates)}")
                    continue
                
                # Usa coordinate start dello step (punto di partenza manovra)
                step_coords = step.coordinates.get('start')
                
                # Verifica che start sia un dict valido
                if step_coords and not isinstance(step_coords, dict):
                    logger.debug(f"Route manager: step {index} start non è un dict: {type(step_coords)}")
                    step_coords = None
                
                # Fallback: se start è null, prova a usare end o geometry
                if not step_coords or not step_coords.get('lat'):
                    # Prova end
                    step_coords = step.coordinates.get('end')
                    if step_coords and isinstance(step_coords, dict) and step_coords.get('lat'):
                        logger.debug(f"Route manager: step {index} usa coordinate end (start non disponibile)")
                    else:
                        step_coords = None
                
                # Se ancora non ha coordinate, prova geometry
                if not step_coords or not step_coords.get('lat'):
                    geometry = step.coordinates.get('geometry', [])
                    if geometry and isinstance(geometry, list) and len(geometry) > 0:
                        # Usa il primo punto della geometry
                        first_point = geometry[0]
                        if isinstance(first_point, (list, tuple)) and len(first_point) >= 2:
                            step_coords = {'lat': float(first_point[0]), 'lng': float(first_point[1])}
                            logger.debug(f"Route manager: step {index} usa coordinate geometry (start/end non disponibili)")
                        else:
                            step_coords = None
                    else:
                        step_coords = None
                
                if not step_coords:
                    # Log dettagliato solo per i primi 2 step per debug
                    if index < 2:
                        logger.debug(f"Route manager: step {index} senza coordinate - coordinates keys: {list(step.coordinates.keys()) if step.coordinates else 'None'}, start: {step.coordinates.get('start') if step.coordinates else None}")
                    continue
                
                step_lat = float(step_coords.get('lat', 0))
                step_lng = float(step_coords.get('lng', 0))
                
                if step_lat == 0 or step_lng == 0:
                    logger.debug(f"Route manager: step {index} coordinate invalide (lat={step_lat}, lng={step_lng})")
                    continue
                
                steps_with_coords += 1
                
                # Calcola distanza da questo step
                distance = calculate_distance(
                    gps_position.latitude,
                    gps_position.longitude,
                    step_lat,
                    step_lng
                )
                
                # Considera anche la direzione di movimento se disponibile
                # Se abbiamo un bearing, preferiamo step nella direzione di movimento
                if gps_position.course and step.bearing:
                    # Calcola differenza angolare (0-180 gradi)
                    bearing_diff = abs(gps_position.course - step.bearing)
                    if bearing_diff > 180:
                        bearing_diff = 360 - bearing_diff
                    
                    # Se la direzione è molto diversa (>90 gradi), penalizza lo step
                    if bearing_diff > 90:
                        distance *= 1.5  # Penalizza step non allineati
                
                if distance < closest_distance:
                    closest_distance = distance
                    closest_step = step
                    closest_index = index
            
            logger.debug(f"Route manager: {steps_with_coords} steps con coordinate valide, closest: index={closest_index}, distance={closest_distance:.0f}m")
            
            # Aggiorna step corrente se trovato uno più vicino
            if closest_step:
                if closest_index != self.current_step_index:
                    self.current_step_index = closest_index
                    self.current_step = closest_step
                    self.last_step_update_time = current_time
                    self.stats['step_updates'] += 1
                    
                    logger.info(f"📍 Step corrente aggiornato: {closest_index + 1}/{len(self.route_steps)} - "
                               f"{closest_step.instruction[:50]}... (distanza: {closest_distance:.0f}m)")
                else:
                    logger.debug(f"Route manager: step già corrente (index={closest_index})")
            else:
                logger.warning(f"⚠️ Route manager: nessuno step trovato con coordinate valide!")
                
                # Se non trova step, verifica se c'è anche una deviazione significativa
                # In questo caso potrebbe essere necessario ricalcolo percorso
                # (ma il ricalcolo verrà gestito in check_deviation/update_position)
                if self.route_geometry and len(self.route_geometry) > 0:
                    # Calcola distanza dal percorso per vedere se serve ricalcolo
                    route_distance = self.calculate_route_distance(gps_position)
                    if route_distance > self.deviation_threshold_recalculate:
                        logger.warning(f"🚨 Nessuno step trovato E deviazione > {self.deviation_threshold_recalculate}m - "
                                     f"Ricalcolo necessario (distanza: {route_distance:.0f}m)")
                        # Il ricalcolo verrà gestito in update_position() quando check_deviation() rileva la deviazione
                else:
                    logger.warning(f"⚠️ Nessuno step trovato E routeGeometry vuoto - "
                                 f"Percorso potrebbe essere malformato o incompleto")                

                
            
            return self.current_step
            
        except Exception as e:
            logger.error(f"Errore calcolo step corrente: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None
    
    def calculate_remaining_distance(self, gps_position: GPSPosition) -> Optional[float]:
        """
        Calcola la distanza rimanente dalla posizione GPS corrente al punto di arrivo dello step corrente
        
        Args:
            gps_position: Posizione GPS corrente
            
        Returns:
            float: Distanza rimanente in metri, o None se non calcolabile
        """
        if not self.current_step or not gps_position.is_valid:
            return None
        
        try:
            # Ottieni coordinate di arrivo dello step
            step_coords = None
            coordinates = self.current_step.coordinates
            
            if isinstance(coordinates, dict):
                # Prova prima con end (punto di arrivo)
                step_coords = coordinates.get('end')
                if step_coords and isinstance(step_coords, dict) and step_coords.get('lat'):
                    pass  # Usa end
                else:
                    # Fallback: usa geometry (ultimo punto)
                    geometry = coordinates.get('geometry', [])
                    if geometry and isinstance(geometry, list) and len(geometry) > 0:
                        last_point = geometry[-1]
                        if isinstance(last_point, (list, tuple)) and len(last_point) >= 2:
                            step_coords = {'lat': float(last_point[0]), 'lng': float(last_point[1])}
                        else:
                            step_coords = None
                    else:
                        step_coords = None
                
                # Se ancora non abbiamo coordinate, prova con start come fallback
                if not step_coords or not step_coords.get('lat'):
                    step_coords = coordinates.get('start')
                    if step_coords and isinstance(step_coords, dict) and step_coords.get('lat'):
                        pass  # Usa start come fallback
                    else:
                        step_coords = None
            
            if not step_coords or not step_coords.get('lat'):
                logger.debug(f"Route manager: impossibile calcolare distanza rimanente - coordinate step non disponibili")
                return None
            
            step_lat = float(step_coords.get('lat', 0))
            step_lng = float(step_coords.get('lng', 0))
            
            if step_lat == 0 or step_lng == 0:
                return None
            
            # Calcola distanza dalla posizione GPS corrente al punto di arrivo dello step
            remaining_distance = calculate_distance(
                gps_position.latitude,
                gps_position.longitude,
                step_lat,
                step_lng
            )
            
            return remaining_distance
            
        except Exception as e:
            logger.debug(f"Errore calcolo distanza rimanente: {e}")
            return None
    
    def calculate_route_distance(self, gps_position: GPSPosition) -> float:
        """
        Calcola la distanza minima dal percorso completo
        
        Args:
            gps_position: Posizione GPS corrente
            
        Returns:
            float: Distanza minima dal percorso in metri (0 se percorso non valido)
        """
        if not self.route_geometry or len(self.route_geometry) < 2:
            return 0.0
        
        if not gps_position.is_valid:
            return 0.0
        
        try:
            min_distance = float('inf')
            
            # Itera su tutti i segmenti del percorso
            for i in range(len(self.route_geometry) - 1):
                point1 = self.route_geometry[i]
                point2 = self.route_geometry[i + 1]
                
                # Calcola distanza punto-segmento
                segment_distance = self._point_to_segment_distance(
                    (gps_position.latitude, gps_position.longitude),
                    point1,
                    point2
                )
                
                if segment_distance < min_distance:
                    min_distance = segment_distance
            
            self.stats['deviation_checks'] += 1
            return min_distance
            
        except Exception as e:
            logger.error(f"Errore calcolo distanza percorso: {e}")
            return 0.0
    
    def _point_to_segment_distance(self, point: Tuple[float, float], 
                                   seg_start: Tuple[float, float], 
                                   seg_end: Tuple[float, float]) -> float:
        """
        Calcola distanza punto-segmento (formula distanza punto-linea)
        
        Args:
            point: Punto GPS (lat, lng)
            seg_start: Inizio segmento (lat, lng)
            seg_end: Fine segmento (lat, lng)
            
        Returns:
            float: Distanza in metri
        """
        try:
            # Converti coordinate in metri usando approssimazione locale
            # Per piccole distanze, possiamo usare coordinate cartesiane
            # Prendiamo il punto medio del segmento come riferimento
            
            # Calcola distanze da punto a estremi del segmento
            dist_to_start = calculate_distance(point[0], point[1], 
                                               seg_start[0], seg_start[1])
            dist_to_end = calculate_distance(point[0], point[1], 
                                             seg_end[0], seg_end[1])
            
            # Lunghezza del segmento
            seg_length = calculate_distance(seg_start[0], seg_start[1], 
                                           seg_end[0], seg_end[1])
            
            # Se il segmento è molto corto, usa distanza dal punto medio
            if seg_length < 10:  # Meno di 10 metri
                mid_lat = (seg_start[0] + seg_end[0]) / 2
                mid_lng = (seg_start[1] + seg_end[1]) / 2
                return calculate_distance(point[0], point[1], mid_lat, mid_lng)
            
            # Calcola proiezione ortogonale del punto sul segmento
            # Usa formula punto-linea in coordinate sferiche (approssimata)
            # Per semplicità, usiamo distanza dal punto più vicino tra start e end
            # oppure calcoliamo proiezione usando prodotto scalare
            
            # Versione semplificata: distanza minima tra punto e estremi del segmento
            # oppure distanza perpendicolare se il punto è "dentro" il segmento
            
            # Calcola distanza perpendicolare usando formula punto-linea
            # Per coordinate sferiche, usiamo approssimazione locale
            
            # Calcola angoli
            lat1, lon1 = seg_start
            lat2, lon2 = seg_end
            lat_p, lon_p = point
            
            # Vettore segmento
            dlat_seg = lat2 - lat1
            dlon_seg = lon2 - lon1
            
            # Vettore punto-start
            dlat_point = lat_p - lat1
            dlon_point = lon_p - lon1
            
            # Prodotto scalare normalizzato (proiezione)
            seg_length_deg = math.sqrt(dlat_seg**2 + dlon_seg**2)
            if seg_length_deg == 0:
                return dist_to_start
            
            # Parametro t (0-1) che indica posizione proiezione sul segmento
            t = (dlat_point * dlat_seg + dlon_point * dlon_seg) / (seg_length_deg**2)
            
            # Limita t tra 0 e 1
            t = max(0, min(1, t))
            
            # Punto proiezione
            proj_lat = lat1 + t * dlat_seg
            proj_lon = lon1 + t * dlon_seg
            
            # Distanza dal punto proiettato
            proj_distance = calculate_distance(lat_p, lon_p, proj_lat, proj_lon)
            
            return proj_distance
            
        except Exception as e:
            logger.debug(f"Errore calcolo distanza punto-segmento: {e}")
            # Fallback: distanza minima tra punto e estremi
            dist_to_start = calculate_distance(point[0], point[1], 
                                               seg_start[0], seg_start[1])
            dist_to_end = calculate_distance(point[0], point[1], 
                                             seg_end[0], seg_end[1])
            return min(dist_to_start, dist_to_end)
    
    def check_deviation(self, gps_position: GPSPosition) -> Optional[RouteDeviation]:
        """
        Verifica se c'è una deviazione significativa dal percorso
        
        Args:
            gps_position: Posizione GPS corrente
            
        Returns:
            RouteDeviation: Dati deviazione o None se non deviato
        """
        if not self.current_route:
            return None
        
        try:
            # Calcola distanza dal percorso
            route_distance = self.calculate_route_distance(gps_position)
            
            if route_distance == 0.0:
                return None
            
            # Crea oggetto deviazione
            deviation = RouteDeviation(
                distance=route_distance,
                threshold_warning=self.deviation_threshold_warning,
                threshold_recalculate=self.deviation_threshold_recalculate,
                is_deviated=route_distance > self.deviation_threshold_warning
            )
            
            # Aggiorna deviazione corrente
            old_deviation = self.deviation
            self.deviation = deviation
            
            # Log se deviazione significativa
            if deviation.is_deviated:
                if not old_deviation or old_deviation.distance < self.deviation_threshold_warning:
                    # Nuova deviazione rilevata
                    logger.warning(f"⚠️ Deviazione rilevata: {route_distance:.0f}m dal percorso")
                    self.stats['warnings'] += 1
                
                if route_distance > self.deviation_threshold_recalculate:
                    logger.warning(f"🚨 Deviazione significativa: {route_distance:.0f}m "
                                 f"(soglia ricalcolo: {self.deviation_threshold_recalculate}m)")
                    self.stats['recalculate_requests'] += 1
            else:
                # Deviazione risolta
                if old_deviation and old_deviation.is_deviated:
                    logger.info(f"✅ Deviazione risolta: ora a {route_distance:.0f}m dal percorso")
            
            return deviation
            
        except Exception as e:
            logger.error(f"Errore verifica deviazione: {e}")
            return None
    
    def update_position(self, gps_position: GPSPosition) -> Dict[str, Any]:
        """
        Aggiorna posizione GPS e calcola step corrente e deviazione
        
        Args:
            gps_position: Posizione GPS corrente
            
        Returns:
            Dict: Dati aggiornamento (step, deviation, remaining_distance)
        """
        result = {
            'step_updated': False,
            'current_step': None,
            'deviation': None,
            'remaining_distance': None
        }
        
        try:
            # Salva indice step corrente prima dell'aggiornamento
            old_step_index = self.current_step_index
            
            # Calcola step corrente (questo aggiorna self.current_step_index se necessario)
            current_step = self.calculate_current_step(gps_position)
            if current_step:
                result['current_step'] = current_step
                # Verifica se lo step è cambiato
                result['step_updated'] = (old_step_index != self.current_step_index)
                
                # Calcola distanza rimanente allo step (sempre, anche se lo step non è cambiato)
                remaining_distance = self.calculate_remaining_distance(gps_position)
                if remaining_distance is not None:
                    result['remaining_distance'] = remaining_distance
            
            # Verifica deviazione
            deviation = self.check_deviation(gps_position)
            if deviation:
                result['deviation'] = deviation
                
                # Se deviazione supera soglia ricalcolo, richiedi ricalcolo
                if deviation.distance > deviation.threshold_recalculate:
                    result['recalculate_needed'] = True
                    result['deviation_distance'] = deviation.distance
            
            # Aggiorna statistiche
            self.stats['last_update'] = datetime.now()
            
            return result
            
        except Exception as e:
            logger.error(f"Errore aggiornamento posizione: {e}")
            return result
    
    def get_current_step(self) -> Optional[RouteStep]:
        """
        Ottiene lo step corrente
        
        Returns:
            RouteStep: Step corrente o None
        """
        return self.current_step
    
    def get_deviation(self) -> Optional[RouteDeviation]:
        """
        Ottiene la deviazione corrente
        
        Returns:
            RouteDeviation: Deviazione corrente o None
        """
        return self.deviation
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Ottiene le statistiche del route manager
        
        Returns:
            Dict: Statistiche
        """
        return {
            'has_route': self.current_route is not None,
            'steps_count': len(self.route_steps),
            'geometry_points': len(self.route_geometry),
            'current_step_index': self.current_step_index,
            'deviation': self.deviation.distance if self.deviation else 0.0,
            'stats': self.stats.copy()
        }
    
    def has_route(self) -> bool:
        """
        Verifica se c'è un percorso attivo
        
        Returns:
            bool: True se percorso attivo
        """
        return self.current_route is not None
    
    def recalculate_route(self, gps_position: GPSPosition) -> Optional[Dict[str, Any]]:
        """
        Ricalcola il percorso automaticamente chiamando API Mapbox
        
        Args:
            gps_position: Posizione GPS corrente
            
        Returns:
            Dict: Nuovo percorso in formato MQTT o None se errore
        """
        if not self.mapbox_enabled:
            logger.warning("⚠️ Ricalcolo automatico disabilitato in configurazione")
            return None
        
        # Verifica che il token sia presente e non vuoto
        if not self.mapbox_access_token or not self.mapbox_access_token.strip():
            logger.error("❌ API Key Mapbox non configurata - impossibile ricalcolare percorso")
            logger.error("   Verifica che MAPBOX_ACCESS_TOKEN sia presente nel file .env e non sia vuoto")
            return None
        
        if not self.destination_coords:
            logger.error("❌ Destinazione non disponibile - impossibile ricalcolare percorso")
            return None
        
        if not gps_position.is_valid:
            logger.warning("⚠️ GPS non valido - impossibile ricalcolare percorso")
            return None
        
        # Cooldown: evita ricalcoli troppo frequenti
        current_time = time.time()
        if self.recalculating:
            logger.debug("Ricalcolo già in corso, salto richiesta")
            return None
        
        if current_time - self.last_recalculate_time < self.recalculate_cooldown:
            logger.debug(f"Ricalcolo in cooldown (mancano {self.recalculate_cooldown - (current_time - self.last_recalculate_time):.1f}s)")
            return None
        
        try:
            self.recalculating = True
            self.last_recalculate_time = current_time
            
            logger.info(f"🔄 Ricalcolo percorso automatico...")
            logger.info(f"   Posizione corrente: {gps_position.latitude:.6f}, {gps_position.longitude:.6f}")
            logger.info(f"   Destinazione: {self.destination_address} ({self.destination_coords})")
            
            # Costruisci URL API Mapbox
            coordinates = f"{gps_position.longitude},{gps_position.latitude};{self.destination_coords['lng']},{self.destination_coords['lat']}"
            url = f"{self.mapbox_api_base_url}/mapbox/{self.mapbox_routing_profile}/{coordinates}"
            
            # Parametri query
            params = {
                'access_token': self.mapbox_access_token,
                'geometries': 'geojson',
                'overview': 'full',
                'steps': 'true',
                'language': self.mapbox_language,
                'annotations': 'duration,distance'
            }
            
            url_with_params = f"{url}?{urllib.parse.urlencode(params)}"
            
            logger.debug(f"Chiamata API Mapbox: {url_with_params.replace(self.mapbox_access_token, '***')}")
            
            # Chiamata API
            request = urllib.request.Request(url_with_params)
            request.add_header('User-Agent', 'MicroNav-RaspberryPi/0.2')
            
            with urllib.request.urlopen(request, timeout=self.mapbox_timeout) as response:
                response_data = json.loads(response.read().decode('utf-8'))
                
                # Verifica risposta
                if response.getcode() != 200:
                    logger.error(f"❌ Errore API Mapbox: HTTP {response.getcode()}")
                    self.stats['recalculate_failed'] += 1
                    return None
                
                # Parsing risposta
                if not response_data.get('routes') or len(response_data['routes']) == 0:
                    logger.error("❌ Nessun percorso trovato nella risposta API Mapbox")
                    self.stats['recalculate_failed'] += 1
                    return None
                
                route = response_data['routes'][0]
                legs = route.get('legs', [])
                if not legs or len(legs) == 0:
                    logger.error("❌ Nessuna leg trovata nella risposta API Mapbox")
                    self.stats['recalculate_failed'] += 1
                    return None
                
                leg = legs[0]
                steps = leg.get('steps', [])
                
                # Estrai coordinate percorso completo
                route_geometry = route.get('geometry', {}).get('coordinates', [])
                
                # Converti risposta API nel formato MQTT esistente
                steps_with_coords = []
                for index, step in enumerate(steps):
                    step_geometry = step.get('geometry', {}).get('coordinates', [])
                    
                    # Punto di partenza dello step
                    start_point = None
                    if step_geometry and len(step_geometry) > 0:
                        start_point = step_geometry[0]
                    elif step.get('maneuver', {}).get('location'):
                        start_point = step['maneuver']['location']
                    
                    # Punto di arrivo dello step
                    end_point = None
                    if step_geometry and len(step_geometry) > 0:
                        end_point = step_geometry[-1]
                    elif index < len(steps) - 1:
                        next_step = steps[index + 1]
                        if next_step.get('maneuver', {}).get('location'):
                            end_point = next_step['maneuver']['location']
                    elif step.get('maneuver', {}).get('location'):
                        end_point = step['maneuver']['location']
                    
                    maneuver = step.get('maneuver', {})
                    
                    step_data = {
                        'instruction': maneuver.get('instruction', ''),
                        'distance': round(step.get('distance', 0)),
                        'duration': round(step.get('duration', 0) / 60),  # Converti in minuti
                        'maneuver': {
                            'type': maneuver.get('type', ''),
                            'modifier': maneuver.get('modifier', ''),
                            'bearing': maneuver.get('bearing_after')
                        },
                        'icon': '',  # Verrà calcolato dal JavaScript o dal display controller
                        'coordinates': {
                            'start': {'lat': start_point[1], 'lng': start_point[0]} if start_point else None,
                            'end': {'lat': end_point[1], 'lng': end_point[0]} if end_point else None,
                            'geometry': [[coord[1], coord[0]] for coord in step_geometry]  # [lat, lng]
                        }
                    }
                    steps_with_coords.append(step_data)
                
                # Costruisci nuovo percorso in formato MQTT
                new_route = {
                    'type': 'route',
                    'origin': f"Posizione corrente ({gps_position.latitude:.6f}, {gps_position.longitude:.6f})",
                    'originCoords': {'lat': gps_position.latitude, 'lng': gps_position.longitude},
                    'destination': self.destination_address or 'Destinazione',
                    'destCoords': self.destination_coords,
                    'totalDistance': round(route.get('distance', 0)),
                    'totalDuration': round(route.get('duration', 0)),
                    'timestamp': get_timestamp_ms(),
                    'routeGeometry': [[coord[1], coord[0]] for coord in route_geometry],  # [lat, lng]
                    'steps': steps_with_coords,
                    'recalculated': True,  # Flag per indicare che è un ricalcolo
                    'old_route': {
                        'origin': self.current_route.get('origin') if self.current_route else None,
                        'destination': self.destination_address
                    } if self.current_route else None
                }
                
                logger.info(f"✅ Percorso ricalcolato: {len(steps_with_coords)} steps, "
                           f"{len(route_geometry)} punti percorso, "
                           f"distanza: {new_route['totalDistance']}m, durata: {new_route['totalDuration']}s")
                
                self.stats['recalculate_success'] += 1
                
                # Notifica callback se presente
                if self.on_route_recalculated:
                    try:
                        self.on_route_recalculated(new_route)
                    except Exception as e:
                        logger.error(f"Errore callback ricalcolo: {e}")
                
                return new_route
                
        except urllib.error.HTTPError as e:
            logger.error(f"❌ Errore HTTP API Mapbox: {e.code} - {e.reason}")
            if e.code == 401:
                logger.error("❌ API Key Mapbox non valida")
            elif e.code == 429:
                logger.error("❌ Rate limit API Mapbox raggiunto")
            self.stats['recalculate_failed'] += 1
            return None
        except urllib.error.URLError as e:
            logger.error(f"❌ Errore connessione API Mapbox: {e.reason}")
            self.stats['recalculate_failed'] += 1
            return None
        except json.JSONDecodeError as e:
            logger.error(f"❌ Errore parsing risposta API Mapbox: {e}")
            self.stats['recalculate_failed'] += 1
            return None
        except Exception as e:
            logger.error(f"❌ Errore ricalcolo percorso: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            self.stats['recalculate_failed'] += 1
            return None
        finally:
            self.recalculating = False

