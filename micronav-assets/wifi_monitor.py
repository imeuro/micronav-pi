#!/usr/bin/env python3
"""
Monitor WiFi per MicroNav Raspberry Pi
Gestisce la connettività dual-mode (WiFi casa + hotspot smartphone)
"""

import time
import logging
import subprocess
import threading
from typing import Dict, List, Optional, Callable
from datetime import datetime

try:
    import nmcli
except ImportError:
    print("❌ Libreria nmcli non trovata. Installa con: pip install python-nmcli")
    exit(1)

# Importa configurazione WiFi
try:
    from config import get_wifi_config
except ImportError:
    print("❌ File config.py non trovato. Assicurati che sia nella stessa directory.")
    exit(1)

# Configurazione logging
logging.basicConfig(
    level=logging.DEBUG,  # Cambiato a DEBUG per più dettagli
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class MicroNavWiFiMonitor:
    """Monitor per gestione WiFi dual-mode"""
    
    def __init__(self, config: Dict):
        """
        Inizializza il monitor WiFi
        
        Args:
            config: Configurazione WiFi
        """
        self.config = config
        self.current_network = None
        self.is_connected = False
        self.has_internet = False
        self.running = False
        self.monitor_thread = None
        
        # Callback per eventi di connessione
        self.connection_callbacks = []
        self.disconnection_callbacks = []
        
        # Statistiche
        self.stats = {
            'connection_attempts': 0,
            'successful_connections': 0,
            'failed_connections': 0,
            'last_connection_time': None,
            'last_disconnection_time': None,
            'uptime': 0,
            'start_time': time.time()
        }
        
        # Reti configurate
        self.networks = {
            'home': config.get('home_network', {}),
            'mobile': config.get('mobile_network', {})
        }
        
        # Verifica ambiente Raspberry Pi
        self._check_raspberry_environment()
        
        logger.info("WiFi Monitor inizializzato")
    
    def _check_raspberry_environment(self):
        """Verifica e configura l'ambiente Raspberry Pi"""
        logger.info("🔍 Verifica ambiente Raspberry Pi...")
        
        # Verifica se siamo su Raspberry Pi
        try:
            with open('/proc/cpuinfo', 'r') as f:
                cpuinfo = f.read()
                if 'BCM' in cpuinfo or 'Raspberry Pi' in cpuinfo:
                    logger.info("✅ Rilevato Raspberry Pi")
                else:
                    logger.warning("⚠️ Non sembra essere un Raspberry Pi")
        except Exception as e:
            logger.warning(f"⚠️ Impossibile verificare tipo CPU: {e}")
        
        # Verifica interfaccia WiFi
        try:
            result = subprocess.run(
                ['nmcli', 'dev', 'status'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                logger.info("✅ NetworkManager disponibile")
                # Cerca interfaccia WiFi
                wifi_devices = []
                for line in result.stdout.split('\n'):
                    if 'wifi' in line.lower():
                        wifi_devices.append(line.strip())
                
                if wifi_devices:
                    logger.info(f"📡 Interfacce WiFi trovate: {len(wifi_devices)}")
                    for device in wifi_devices:
                        logger.info(f"  - {device}")
                else:
                    logger.warning("⚠️ Nessuna interfaccia WiFi trovata")
            else:
                logger.error(f"❌ NetworkManager non disponibile: {result.stderr}")
        except Exception as e:
            logger.error(f"❌ Errore verifica NetworkManager: {e}")
        
        # Verifica permessi
        try:
            result = subprocess.run(
                ['whoami'],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                user = result.stdout.strip()
                logger.info(f"👤 Utente corrente: {user}")
                if user == 'root':
                    logger.info("✅ Esecuzione come root - permessi completi")
                else:
                    logger.info("ℹ️ Esecuzione come utente normale - userò sudo se necessario")
        except Exception as e:
            logger.warning(f"⚠️ Impossibile verificare utente: {e}")
        
        # Verifica se WiFi è abilitato
        try:
            result = subprocess.run(
                ['nmcli', 'radio', 'wifi'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                status = result.stdout.strip()
                logger.info(f"📶 Stato WiFi: {status}")
                if status == 'disabled':
                    logger.warning("⚠️ WiFi disabilitato - tentativo abilitazione...")
                    enable_result = subprocess.run(
                        ['nmcli', 'radio', 'wifi', 'on'],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if enable_result.returncode == 0:
                        logger.info("✅ WiFi abilitato")
                    else:
                        logger.error(f"❌ Impossibile abilitare WiFi: {enable_result.stderr}")
            else:
                logger.error(f"❌ Impossibile verificare stato WiFi: {result.stderr}")
        except Exception as e:
            logger.error(f"❌ Errore verifica stato WiFi: {e}")
    
    
    def add_connection_callback(self, callback: Callable):
        """Aggiunge callback per eventi di connessione"""
        self.connection_callbacks.append(callback)
        logger.info("Callback connessione registrato")
    
    def add_disconnection_callback(self, callback: Callable):
        """Aggiunge callback per eventi di disconnessione"""
        self.disconnection_callbacks.append(callback)
        logger.info("Callback disconnessione registrato")
    
    def _trigger_connection_callbacks(self, network_name: str, network_info: Dict):
        """Triggera i callback di connessione"""
        for callback in self.connection_callbacks:
            try:
                callback(network_name, network_info)
            except Exception as e:
                logger.error(f"Errore callback connessione: {e}")
    
    def _trigger_disconnection_callbacks(self, network_name: str):
        """Triggera i callback di disconnessione"""
        for callback in self.disconnection_callbacks:
            try:
                callback(network_name)
            except Exception as e:
                logger.error(f"Errore callback disconnessione: {e}")
    
    def get_available_networks(self) -> List[Dict]:
        """Ottiene la lista delle reti WiFi disponibili"""
        try:
            logger.debug("🔍 Avvio scansione reti WiFi...")
            
            # Metodo 1: Scansione standard
            networks = self._scan_networks_standard()
            
            # Se non trova reti, prova metodi alternativi
            if not networks:
                logger.warning("⚠️ Nessuna rete trovata con metodo standard, provo metodi alternativi...")
                
                # Metodo 2: Forza scansione e riprova
                networks = self._scan_networks_with_rescan()
                
                # Metodo 3: Usa formato diverso
                if not networks:
                    networks = self._scan_networks_alternative_format()
            
            logger.info(f"📡 Trovate {len(networks)} reti WiFi disponibili")
            return networks
            
        except Exception as e:
            logger.error(f"❌ Errore scansione reti WiFi: {e}")
            return []
    
    def _scan_networks_standard(self) -> List[Dict]:
        """Scansione standard con nmcli"""
        try:
            result = subprocess.run(
                ['nmcli', '-t', '-f', 'SSID,SIGNAL,SECURITY', 'dev', 'wifi', 'list'],
                capture_output=True,
                text=True,
                timeout=15
            )
            
            networks = []
            if result.returncode == 0 and result.stdout.strip():
                logger.debug(f"Output nmcli standard: {result.stdout}")
                networks = self._parse_network_output(result.stdout)
            
            return networks
        except Exception as e:
            logger.debug(f"Metodo standard fallito: {e}")
            return []
    
    def _scan_networks_with_rescan(self) -> List[Dict]:
        """Scansione con rescan forzato"""
        try:
            logger.info("🔄 Forza scansione WiFi...")
            scan_result = subprocess.run(
                ['nmcli', 'dev', 'wifi', 'rescan'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if scan_result.returncode == 0:
                logger.info("✅ Scansione forzata completata")
                # Riprova la lista dopo la scansione
                result = subprocess.run(
                    ['nmcli', '-t', '-f', 'SSID,SIGNAL,SECURITY', 'dev', 'wifi', 'list'],
                    capture_output=True,
                    text=True,
                    timeout=15
                )
                
                if result.returncode == 0 and result.stdout.strip():
                    logger.debug(f"Output dopo rescan: {result.stdout}")
                    return self._parse_network_output(result.stdout)
            else:
                logger.warning(f"❌ Rescan fallito: {scan_result.stderr}")
            
            return []
        except Exception as e:
            logger.debug(f"Metodo con rescan fallito: {e}")
            return []
    
    def _scan_networks_alternative_format(self) -> List[Dict]:
        """Scansione con formato alternativo"""
        try:
            logger.info("🔄 Provo formato alternativo...")
            result = subprocess.run(
                ['nmcli', '-t', '-f', 'SSID,SIGNAL,SECURITY,IN-USE', 'dev', 'wifi', 'list'],
                capture_output=True,
                text=True,
                timeout=15
            )
            
            networks = []
            if result.returncode == 0 and result.stdout.strip():
                logger.debug(f"Output formato alternativo: {result.stdout}")
                networks = self._parse_network_output(result.stdout)
            
            return networks
        except Exception as e:
            logger.debug(f"Metodo formato alternativo fallito: {e}")
            return []
    
    def _parse_network_output(self, output: str) -> List[Dict]:
        """Parsa l'output di nmcli per estrarre le reti"""
        networks = []
        
        for line in output.strip().split('\n'):
            if line and line != '--':
                parts = line.split(':')
                if len(parts) >= 2:
                    ssid = parts[0] if parts[0] != '--' else ''
                    signal_str = parts[1] if len(parts) > 1 and parts[1] != '--' else '0'
                    security = parts[2] if len(parts) > 2 and parts[2] != '--' else 'Open'
                    
                    # Pulisci il segnale
                    try:
                        signal = int(signal_str) if signal_str.isdigit() else 0
                    except ValueError:
                        signal = 0
                    
                    if ssid:  # Solo se c'è un SSID valido
                        networks.append({
                            'ssid': ssid,
                            'signal': signal,
                            'security': security
                        })
                        logger.debug(f"📶 Rete trovata: {ssid} (segnale: {signal}%, sicurezza: {security})")
        
        return networks
    
    def get_current_connection(self) -> Optional[Dict]:
        """Ottiene informazioni sulla connessione corrente"""
        try:
            # Ottieni connessione attiva
            result = subprocess.run(
                ['nmcli', '-t', '-f', 'NAME,TYPE,DEVICE,STATE', 'con', 'show', '--active'],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode != 0:
                logger.debug(f"Errore nmcli con show: {result.stderr}")
                return None
            
            logger.debug(f"Connessioni attive: {result.stdout}")
            
            for line in result.stdout.strip().split('\n'):
                if line and 'wifi' in line:
                    parts = line.split(':')
                    if len(parts) >= 4:
                        connection_info = {
                            'name': parts[0],
                            'type': parts[1],
                            'device': parts[2],
                            'state': parts[3]
                        }
                        logger.debug(f"Connessione WiFi trovata: {connection_info}")
                        return connection_info
            
            logger.debug("Nessuna connessione WiFi attiva trovata")
            return None
            
        except Exception as e:
            logger.error(f"Errore ottenimento connessione corrente: {e}")
            return None
    
    def check_internet_connection(self) -> bool:
        """Verifica se c'è connessione internet"""
        try:
            # Test con ping a server affidabile
            result = subprocess.run(
                ['ping', '-c', '1', '-W', '3', '8.8.8.8'],
                capture_output=True,
                timeout=5
            )
            
            return result.returncode == 0
            
        except subprocess.TimeoutExpired:
            return False
        except Exception as e:
            logger.error(f"Errore test connessione internet: {e}")
            return False
    
    def connect_to_network(self, network_name: str) -> bool:
        """
        Connette a una rete specifica
        
        Args:
            network_name: Nome della rete ('home' o 'mobile')
        """
        if network_name not in self.networks:
            logger.error(f"Rete {network_name} non configurata")
            return False
        
        network_config = self.networks[network_name]
        ssid = network_config.get('ssid')
        password = network_config.get('password')
        security = network_config.get('security', 'wpa-psk')  # Default a WPA-PSK
        
        if not ssid:
            logger.error(f"SSID non configurato per rete {network_name}")
            return False
        
        try:
            self.stats['connection_attempts'] += 1
            logger.info(f"🔗 Tentativo connessione a {ssid} ({network_name})")
            
            # Disconnetti da connessioni attive
            subprocess.run(['nmcli', 'con', 'down', 'id', ssid], 
                         capture_output=True, timeout=5)
            
            # Connetti alla rete con configurazione di sicurezza
            if password:
                # Crea nome connessione sicuro (senza spazi e caratteri speciali)
                safe_connection_name = ssid.replace(' ', '_').replace('-', '_').replace('.', '_')
                
                # Metodo di connessione (connessione permanente con sudo)
                cmd = ['sudo', 'nmcli', 'con', 'add', 'type', 'wifi', 'con-name', safe_connection_name, 
                       'ifname', '*', 'ssid', f'"{ssid}"', 'wifi-sec.key-mgmt', 'wpa-psk', 
                       'wifi-sec.psk', password]
                
                logger.info(f"Tentativo connessione con sudo: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                
                if result.returncode == 0:
                    logger.info("✅ Connessione creata")
                    
                    # Attiva la connessione
                    logger.info("🔄 Attivazione connessione...")
                    activate_cmd = ['sudo', 'nmcli', 'con', 'up', safe_connection_name]
                    activate_result = subprocess.run(activate_cmd, capture_output=True, text=True, timeout=15)
                    
                    if activate_result.returncode == 0:
                        logger.info("✅ Connessione attivata")
                        # Attendi stabilizzazione connessione
                        time.sleep(3)
                        success = True
                    else:
                        logger.error(f"❌ Attivazione fallita: {activate_result.stderr}")
                        self.stats['failed_connections'] += 1
                        return False
                else:
                    error_msg = result.stderr.strip()
                    logger.error(f"❌ Connessione fallita: {error_msg}")
                    self.stats['failed_connections'] += 1
                    return False
                    
            else:
                # Per reti aperte
                cmd = ['nmcli', 'dev', 'wifi', 'connect', ssid]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                
                if result.returncode == 0:
                    logger.info("✅ Connessione rete aperta riuscita")
                    success = True
                else:
                    logger.error(f"❌ Connessione rete aperta fallita: {result.stderr}")
                    self.stats['failed_connections'] += 1
                    return False
            
            # Se arriviamo qui, la connessione è riuscita
            if success:
                logger.info(f"🎉 Connessione WiFi completata con successo!")
                self.current_network = network_name
                self.is_connected = True
                self.stats['successful_connections'] += 1
                self.stats['last_connection_time'] = datetime.now()
                
                logger.info(f"✅ Connesso a {ssid} ({network_name})")
                
                # Verifica connessione internet
                time.sleep(2)  # Attendi stabilizzazione
                self.has_internet = self.check_internet_connection()
                
                if self.has_internet:
                    logger.info("✅ Connessione internet verificata")
                else:
                    logger.warning("⚠️ Connesso ma senza internet")
                
                # Triggera callback
                self._trigger_connection_callbacks(network_name, {
                    'ssid': ssid,
                    'has_internet': self.has_internet,
                    'timestamp': datetime.now()
                })
                
                return True
            else:
                # Se arriviamo qui senza successo, c'è stato un errore
                self.stats['failed_connections'] += 1
                logger.error(f"❌ Connessione fallita a {ssid}")
                return False
                
        except subprocess.TimeoutExpired:
            self.stats['failed_connections'] += 1
            logger.error(f"❌ Timeout connessione a {ssid}")
            return False
        except Exception as e:
            self.stats['failed_connections'] += 1
            logger.error(f"❌ Errore connessione a {ssid}: {e}")
            return False
    
    def disconnect(self):
        """Disconnette dalla rete corrente"""
        try:
            if self.current_network:
                network_config = self.networks[self.current_network]
                ssid = network_config.get('ssid')
                
                if ssid:
                    result = subprocess.run(
                        ['nmcli', 'con', 'down', 'id', ssid],
                        capture_output=True,
                        timeout=5
                    )
                    
                    if result.returncode == 0:
                        logger.info(f"Disconnesso da {ssid}")
                    else:
                        logger.error(f"Errore disconnessione: {result.stderr}")
                
                # Triggera callback
                self._trigger_disconnection_callbacks(self.current_network)
                
                self.current_network = None
                self.is_connected = False
                self.has_internet = False
                self.stats['last_disconnection_time'] = datetime.now()
                
        except Exception as e:
            logger.error(f"Errore durante disconnessione: {e}")
    
    def find_best_network(self) -> Optional[str]:
        """
        Trova la migliore rete disponibile basata su priorità e segnale
        
        Returns:
            Nome della rete ('home' o 'mobile') o None
        """
        logger.debug("Ricerca migliore rete disponibile...")
        
        # Debug: mostra reti configurate
        logger.debug("Reti configurate:")
        for network_name, network_config in self.networks.items():
            ssid = network_config.get('ssid')
            priority = network_config.get('priority', 0)
            logger.debug(f"  {network_name}: {ssid} (priorità: {priority})")
        
        available_networks = self.get_available_networks()
        
        if not available_networks:
            logger.warning("Nessuna rete WiFi disponibile trovata")
            return None
        
        # Debug: mostra reti disponibili
        logger.debug("Reti WiFi disponibili:")
        for net in available_networks:
            logger.debug(f"  {net['ssid']} (segnale: {net['signal']}%, sicurezza: {net['security']})")
        
        # Cerca le reti configurate
        found_networks = []
        for network_name, network_config in self.networks.items():
            ssid = network_config.get('ssid')
            priority = network_config.get('priority', 0)
            
            if not ssid:
                logger.warning(f"SSID non configurato per rete {network_name}")
                continue
            
            logger.debug(f"Cerca rete configurata: {ssid}")
            
            for available in available_networks:
                if available['ssid'] == ssid:
                    found_networks.append({
                        'name': network_name,
                        'ssid': ssid,
                        'signal': available['signal'],
                        'priority': priority
                    })
                    logger.info(f"✅ Rete configurata trovata: {ssid} (segnale: {available['signal']}%)")
                    break
            else:
                logger.debug(f"❌ Rete configurata {ssid} non trovata nelle reti disponibili")
        
        if not found_networks:
            logger.warning("Nessuna rete configurata trovata tra quelle disponibili")
            logger.info("Reti configurate vs disponibili:")
            for network_name, network_config in self.networks.items():
                ssid = network_config.get('ssid')
                if ssid:
                    available_ssids = [net['ssid'] for net in available_networks]
                    logger.info(f"  {ssid} -> {'✅ Trovata' if ssid in available_ssids else '❌ Non trovata'}")
            return None
        
        # Ordina per priorità (maggiore = migliore) e poi per segnale
        found_networks.sort(key=lambda x: (-x['priority'], -x['signal']))
        
        best_network = found_networks[0]
        logger.info(f"🏆 Migliore rete trovata: {best_network['ssid']} "
                   f"(priorità: {best_network['priority']}, "
                   f"segnale: {best_network['signal']}%)")
        
        return best_network['name']
    
    def monitor_loop(self):
        """Loop principale di monitoraggio"""
        logger.info("Avvio monitoraggio WiFi...")
        
        while self.running:
            try:
                # Verifica connessione corrente
                current_conn = self.get_current_connection()
                
                if current_conn and current_conn['state'] == 'activated':
                    # Connesso - verifica internet
                    if not self.is_connected:
                        logger.info(f"✅ Connessione WiFi rilevata: {current_conn['name']}")
                        self.is_connected = True
                        self.current_network = current_conn['name']
                    
                    if not self.has_internet:
                        self.has_internet = self.check_internet_connection()
                        if self.has_internet:
                            logger.info("✅ Connessione internet ripristinata")
                else:
                    # Non connesso - cerca migliore rete
                    if self.is_connected:
                        logger.warning("⚠️ Connessione WiFi persa")
                        self.is_connected = False
                        self.has_internet = False
                        self.current_network = None
                    
                    best_network = self.find_best_network()
                    if best_network:
                        logger.info(f"Tentativo connessione a rete: {best_network}")
                        self.connect_to_network(best_network)
                    else:
                        logger.warning("Nessuna rete configurata disponibile")
                
                # Aggiorna statistiche
                self.stats['uptime'] = time.time() - self.stats['start_time']
                
                # Attendi prima del prossimo controllo
                time.sleep(self.config.get('monitor_interval', 30))
                
            except Exception as e:
                logger.error(f"Errore nel loop di monitoraggio: {e}")
                time.sleep(5)  # Attendi prima di riprovare
    
    def start(self):
        """Avvia il monitor WiFi"""
        if self.running:
            logger.warning("Monitor WiFi già in esecuzione")
            return
        
        self.running = True
        self.stats['start_time'] = time.time()
        
        # Avvia thread di monitoraggio
        self.monitor_thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.monitor_thread.start()
        
        logger.info("✅ Monitor WiFi avviato")
    
    def stop(self):
        """Ferma il monitor WiFi"""
        if not self.running:
            return
        
        logger.info("Arresto monitor WiFi...")
        self.running = False
        
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)
        
        logger.info("✅ Monitor WiFi fermato")
    
    def get_status(self) -> Dict:
        """Restituisce lo status del monitor WiFi"""
        return {
            'running': self.running,
            'is_connected': self.is_connected,
            'has_internet': self.has_internet,
            'current_network': self.current_network,
            'stats': self.stats.copy()
        }
    
    def force_reconnect(self):
        """Forza la riconnessione alla migliore rete disponibile"""
        logger.info("Forzatura riconnessione WiFi...")
        
        if self.is_connected:
            self.disconnect()
        
        time.sleep(2)
        best_network = self.find_best_network()
        if best_network:
            return self.connect_to_network(best_network)
        
        return False
    
    def test_connection_manually(self, ssid: str, password: str):
        """
        Testa manualmente la connessione a una rete specifica
        Utile per debug e test
        """
        logger.info(f"Test connessione manuale a: {ssid}")
        
        # Crea nome connessione sicuro
        safe_connection_name = ssid.replace(' ', '_').replace('-', '_').replace('.', '_')
        
        # Metodo di test (connessione permanente con sudo)
        cmd = ['sudo', 'nmcli', 'con', 'add', 'type', 'wifi', 'con-name', f'test_{safe_connection_name}', 
               'ifname', '*', 'ssid', f'"{ssid}"', 'wifi-sec.key-mgmt', 'wpa-psk', 
               'wifi-sec.psk', password]
        
        try:
            logger.info(f"Test connessione con sudo: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            
            if result.returncode == 0:
                logger.info("✅ Test connessione riuscito!")
                logger.info(f"Output: {result.stdout}")
                return True
            else:
                error_msg = result.stderr.strip()
                logger.error(f"❌ Test connessione fallito: {error_msg}")
                return False
                    
        except subprocess.TimeoutExpired:
            logger.warning("❌ Timeout test connessione")
            return False
        except Exception as e:
            logger.warning(f"❌ Errore test connessione: {e}")
            return False
    
    def debug_network_scan(self):
        """
        Metodo di debug per testare la scansione reti
        Utile per diagnosticare problemi di rilevamento
        """
        logger.info("🔍 DEBUG: Avvio scansione reti WiFi...")
        
        # Test 1: Verifica stato interfaccia WiFi
        logger.info("Test 1: Stato interfaccia WiFi")
        try:
            result = subprocess.run(
                ['nmcli', 'dev', 'status'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                logger.info(f"Stato interfacce:\n{result.stdout}")
            else:
                logger.error(f"Errore stato interfacce: {result.stderr}")
        except Exception as e:
            logger.error(f"Errore test stato interfacce: {e}")
        
        # Test 2: Forza scansione
        logger.info("Test 2: Forza scansione WiFi")
        try:
            result = subprocess.run(
                ['nmcli', 'dev', 'wifi', 'rescan'],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                logger.info("✅ Scansione forzata completata")
            else:
                logger.error(f"❌ Errore scansione forzata: {result.stderr}")
        except Exception as e:
            logger.error(f"Errore scansione forzata: {e}")
        
        # Test 3: Lista reti con formato dettagliato
        logger.info("Test 3: Lista reti formato dettagliato")
        try:
            result = subprocess.run(
                ['nmcli', '-t', '-f', 'SSID,SIGNAL,SECURITY,IN-USE', 'dev', 'wifi', 'list'],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                logger.info(f"Reti trovate:\n{result.stdout}")
            else:
                logger.error(f"Errore lista reti: {result.stderr}")
        except Exception as e:
            logger.error(f"Errore lista reti: {e}")
        
        # Test 4: Usa il nostro metodo
        logger.info("Test 4: Usa metodo get_available_networks()")
        networks = self.get_available_networks()
        logger.info(f"Reti trovate dal nostro metodo: {len(networks)}")
        for net in networks:
            logger.info(f"  - {net['ssid']} (segnale: {net['signal']}%, sicurezza: {net['security']})")
        
        # Test 5: Verifica reti configurate (da config.py)
        logger.info("Test 5: Reti configurate (da config.py)")
        for network_name, network_config in self.networks.items():
            ssid = network_config.get('ssid')
            password = network_config.get('password')
            priority = network_config.get('priority', 0)
            logger.info(f"  {network_name}: {ssid} (priorità: {priority}, password: {'✅' if password else '❌'})")
        
        return networks

# Esempio di utilizzo
if __name__ == "__main__":
    import sys
    
    # Carica configurazione WiFi da config.py
    wifi_config = get_wifi_config()
    print(f"📡 Configurazione WiFi caricata da config.py")
    print(f"  Rete Casa: {wifi_config['home_network']['ssid']}")
    print(f"  Rete Mobile: {wifi_config['mobile_network']['ssid']}")
    
    # Crea monitor WiFi
    wifi_monitor = MicroNavWiFiMonitor(wifi_config)
    
    # Se viene passato l'argomento 'debug', esegui solo il debug
    if len(sys.argv) > 1 and sys.argv[1] == 'debug':
        print("🔍 Modalità DEBUG - Test scansione reti...")
        print("🔐 Test connessioni eseguiti con sudo")
        networks = wifi_monitor.debug_network_scan()
        print(f"\n📊 Risultato: {len(networks)} reti trovate")
        for net in networks:
            print(f"  - {net['ssid']} (segnale: {net['signal']}%, sicurezza: {net['security']})")
        sys.exit(0)
    
    # Callback per eventi di connessione
    def on_connected(network_name, network_info):
        print(f"🔗 Connesso a {network_name}: {network_info['ssid']}")
        print(f"   Internet: {'✅' if network_info['has_internet'] else '❌'}")
    
    def on_disconnected(network_name):
        print(f"📴 Disconnesso da {network_name}")
    
    # Registra callback
    wifi_monitor.add_connection_callback(on_connected)
    wifi_monitor.add_disconnection_callback(on_disconnected)
    
    try:
        # Avvia monitor
        wifi_monitor.start()
        print("✅ Monitor WiFi avviato. Premi Ctrl+C per fermare.")
        print("💡 Suggerimento: usa 'python wifi_monitor.py debug' per testare la scansione")
        print("📁 Configurazione caricata da config.py")
        print("🔐 Connessioni WiFi eseguite con sudo per permessi completi")
        
        # Loop principale
        while True:
            time.sleep(10)
            
            # Stampa status ogni 10 secondi
            status = wifi_monitor.get_status()
            print(f"Status: {'Connesso' if status['is_connected'] else 'Disconnesso'} "
                  f"({status['current_network']}) - "
                  f"Internet: {'✅' if status['has_internet'] else '❌'}")
    
    except KeyboardInterrupt:
        print("\n⏹️  Arresto monitor WiFi...")
        wifi_monitor.stop()
        print("✅ Monitor WiFi fermato")
    
    except Exception as e:
        print(f"❌ Errore: {e}")
        wifi_monitor.stop()

