#!/usr/bin/env python3
"""
Script di debug per GPS L76K
Verifica connessione seriale, permessi, configurazione UART e comunicazione GPS
"""

import os
import sys
import time
import subprocess
import stat
from pathlib import Path
from typing import Optional, Dict, Any, List

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("❌ Libreria pyserial non trovata. Installa con: pip install pyserial")
    sys.exit(1)

# Colori per output
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    CYAN = '\033[0;36m'
    MAGENTA = '\033[0;35m'
    NC = '\033[0m'  # No Color

def print_header(text: str):
    """Stampa un'intestazione"""
    print(f"\n{Colors.CYAN}{'='*60}{Colors.NC}")
    print(f"{Colors.CYAN}{text:^60}{Colors.NC}")
    print(f"{Colors.CYAN}{'='*60}{Colors.NC}\n")

def print_section(text: str):
    """Stampa un'intestazione di sezione"""
    print(f"\n{Colors.BLUE}▶ {text}{Colors.NC}")
    print(f"{Colors.BLUE}{'-'*60}{Colors.NC}")

def print_success(text: str):
    """Stampa un messaggio di successo"""
    print(f"{Colors.GREEN}✅ {text}{Colors.NC}")

def print_error(text: str):
    """Stampa un messaggio di errore"""
    print(f"{Colors.RED}❌ {text}{Colors.NC}")

def print_warning(text: str):
    """Stampa un messaggio di avviso"""
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.NC}")

def print_info(text: str):
    """Stampa un messaggio informativo"""
    print(f"{Colors.MAGENTA}ℹ️  {text}{Colors.NC}")

def run_command(cmd: List[str], capture_output: bool = True) -> Optional[str]:
    """Esegue un comando shell e ritorna l'output"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture_output,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip() if capture_output else None
        else:
            return None
    except Exception as e:
        print_warning(f"Errore esecuzione comando {' '.join(cmd)}: {e}")
        return None

class GPSDebugger:
    """Debugger per GPS L76K"""
    
    def __init__(self, port: str = '/dev/ttyS0', baudrate: int = 9600):
        self.port = port
        self.baudrate = baudrate
        self.serial_conn = None
        self.issues = []
        self.fixes = []
    
    def check_device_exists(self) -> bool:
        """Verifica se il dispositivo seriale esiste"""
        print_section("1. Verifica esistenza dispositivo")
        
        if os.path.exists(self.port):
            print_success(f"Dispositivo {self.port} trovato")
            
            # Mostra informazioni sul dispositivo
            try:
                stat_info = os.stat(self.port)
                print_info(f"Tipo: {stat.S_IFMT(stat_info.st_mode)}")
                print_info(f"Permessi: {oct(stat_info.st_mode)[-3:]}")
            except Exception as e:
                print_warning(f"Impossibile leggere informazioni dispositivo: {e}")
            
            return True
        else:
            print_error(f"Dispositivo {self.port} NON trovato")
            self.issues.append(f"Dispositivo {self.port} non esiste")
            
            # Suggerimenti
            print_info("Verifica alternative:")
            alt_ports = ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyACM0', '/dev/ttyAMA0']
            for alt_port in alt_ports:
                if os.path.exists(alt_port):
                    print_info(f"  - {alt_port} trovato (potrebbe essere il GPS)")
            
            return False
    
    def check_permissions(self) -> bool:
        """Verifica i permessi sul dispositivo"""
        print_section("2. Verifica permessi dispositivo")
        
        if not os.path.exists(self.port):
            print_error(f"Dispositivo {self.port} non esiste, salto verifica permessi")
            return False
        
        try:
            # Verifica se possiamo leggere
            if os.access(self.port, os.R_OK):
                print_success(f"Permesso di lettura su {self.port}")
            else:
                print_error(f"Nessun permesso di lettura su {self.port}")
                self.issues.append(f"Permessi di lettura insufficienti su {self.port}")
                self.fixes.append(f"sudo chmod 666 {self.port}")
                self.fixes.append(f"sudo usermod -a -G dialout $USER")
                return False
            
            # Verifica se possiamo scrivere
            if os.access(self.port, os.W_OK):
                print_success(f"Permesso di scrittura su {self.port}")
            else:
                print_error(f"Nessun permesso di scrittura su {self.port}")
                self.issues.append(f"Permessi di scrittura insufficienti su {self.port}")
                self.fixes.append(f"sudo chmod 666 {self.port}")
                return False
            
            # Mostra proprietario e gruppo
            stat_info = os.stat(self.port)
            import pwd
            import grp
            owner = pwd.getpwuid(stat_info.st_uid).pw_name
            group = grp.getgrgid(stat_info.st_gid).gr_name
            print_info(f"Proprietario: {owner}")
            print_info(f"Gruppo: {group}")
            
            # Verifica se l'utente corrente è nel gruppo dialout
            current_user = os.getenv('USER', 'unknown')
            try:
                dialout_group = grp.getgrnam('dialout')
                current_groups = [g.gr_name for g in grp.getgrall() if current_user in g.gr_mem]
                current_gid = os.getgid()
                current_groups.append(grp.getgrgid(current_gid).gr_name)
                
                if 'dialout' in current_groups or group == 'dialout':
                    print_success(f"Utente {current_user} ha accesso al gruppo dialout")
                else:
                    print_warning(f"Utente {current_user} NON è nel gruppo dialout")
                    self.fixes.append(f"sudo usermod -a -G dialout {current_user}")
                    self.fixes.append("(Riavvia la sessione dopo questo comando)")
            except Exception as e:
                print_warning(f"Impossibile verificare gruppo dialout: {e}")
            
            return True
            
        except Exception as e:
            print_error(f"Errore verifica permessi: {e}")
            self.issues.append(f"Errore verifica permessi: {e}")
            return False
    
    def check_processes_using_port(self) -> bool:
        """Verifica se ci sono processi che usano la porta"""
        print_section("3. Verifica processi che usano la porta")
        
        try:
            # Usa lsof per trovare processi che usano la porta
            result = run_command(['lsof', self.port])
            
            if result:
                print_warning(f"Processi che usano {self.port}:")
                print(result)
                self.issues.append(f"Processi attivi su {self.port}")
                self.fixes.append(f"sudo lsof -t {self.port} | xargs sudo kill -9")
                
                # Verifica se è getty o serial-getty (console seriale)
                if 'getty' in result.lower() or 'serial-getty' in result.lower():
                    print_error("Console seriale (getty) attiva sulla porta!")
                    self.issues.append("Console seriale attiva - disabilita in /boot/cmdline.txt")
                    self.fixes.append("Rimuovi 'console=serial0' da /boot/cmdline.txt e riavvia")
                
                return False
            else:
                print_success(f"Nessun processo attivo su {self.port}")
                return True
                
        except Exception as e:
            print_warning(f"Impossibile verificare processi (lsof non disponibile?): {e}")
            # Prova con fuser
            result = run_command(['fuser', self.port])
            if result:
                print_warning(f"Processi che usano {self.port}: {result}")
                return False
            
            # Verifica anche con systemctl se ci sono servizi serial-getty
            getty_service = f"serial-getty@{self.port.replace('/dev/', '')}.service"
            result = run_command(['systemctl', 'is-active', getty_service])
            if result and 'active' in result.lower():
                print_error(f"Servizio {getty_service} attivo!")
                self.issues.append(f"Servizio serial-getty attivo su {self.port}")
                self.fixes.append(f"sudo systemctl stop {getty_service}")
                self.fixes.append(f"sudo systemctl disable {getty_service}")
                return False
            
            return True
    
    def check_uart_config(self) -> bool:
        """Verifica configurazione UART in /boot/config.txt o /boot/firmware/config.txt"""
        print_section("4. Verifica configurazione UART")
        
        # Prova entrambe le posizioni possibili
        config_files = ['/boot/firmware/config.txt', '/boot/config.txt']
        config_file = None
        config_content = None
        
        for cf in config_files:
            if os.path.exists(cf):
                config_file = cf
                print_info(f"Trovato file di configurazione: {cf}")
                break
        
        if not config_file:
            print_warning("Nessun file config.txt trovato (né /boot/config.txt né /boot/firmware/config.txt)")
            print_warning("Potrebbe essere un sistema non-Raspberry Pi o configurazione non standard")
            self.issues.append("File config.txt non trovato")
            return False
        
        try:
            with open(config_file, 'r') as f:
                config_content = f.read()
            
            print_info(f"Analisi file: {config_file}")
            
            # Verifica enable_uart (cerca in tutte le righe, ignorando commenti e spazi)
            enable_uart_found = False
            enable_uart_value = None
            
            for line in config_content.split('\n'):
                # Rimuovi commenti e spazi
                clean_line = line.split('#')[0].strip()
                if clean_line.startswith('enable_uart'):
                    enable_uart_found = True
                    if 'enable_uart=1' in clean_line:
                        enable_uart_value = 1
                        break
                    elif 'enable_uart=0' in clean_line:
                        enable_uart_value = 0
                        break
            
            if enable_uart_found and enable_uart_value == 1:
                print_success(f"enable_uart=1 trovato in {config_file}")
            elif enable_uart_found and enable_uart_value == 0:
                print_error(f"enable_uart=0 trovato in {config_file} - UART disabilitato!")
                self.issues.append(f"UART disabilitato in {config_file}")
                self.fixes.append(f"Cambia 'enable_uart=0' in 'enable_uart=1' in {config_file} e riavvia")
            else:
                print_warning(f"enable_uart non trovato o non configurato correttamente in {config_file}")
                self.issues.append("enable_uart non configurato")
                self.fixes.append(f"Aggiungi 'enable_uart=1' in {config_file} e riavvia")
            
            # Verifica console seriale (deve essere disabilitata)
            # Nota: la console seriale è in cmdline.txt, non in config.txt
            # ma controlliamo comunque per completezza
            if 'console=serial0' in config_content or 'console=ttyS0' in config_content:
                print_warning("Console seriale trovata in config.txt (dovrebbe essere in cmdline.txt)")
            
            # Verifica dtoverlay
            if 'dtoverlay=disable-bt' in config_content:
                print_info("dtoverlay=disable-bt trovato (Bluetooth disabilitato)")
            elif 'dtoverlay=miniuart-bt' in config_content:
                print_info("dtoverlay=miniuart-bt trovato (UART principale libero)")
            
            return True
            
        except PermissionError:
            print_error(f"Permessi insufficienti per leggere {config_file}")
            self.issues.append(f"Impossibile leggere {config_file}")
            return False
        except Exception as e:
            print_error(f"Errore lettura {config_file}: {e}")
            self.issues.append(f"Errore lettura configurazione: {e}")
            return False
    
    def check_cmdline_txt(self) -> bool:
        """Verifica /boot/cmdline.txt o /boot/firmware/cmdline.txt per console seriale"""
        print_section("5. Verifica cmdline.txt")
        
        # Prova entrambe le posizioni possibili
        cmdline_files = ['/boot/firmware/cmdline.txt', '/boot/cmdline.txt']
        cmdline_file = None
        
        for cf in cmdline_files:
            if os.path.exists(cf):
                cmdline_file = cf
                print_info(f"Trovato file cmdline: {cf}")
                break
        
        if not cmdline_file:
            print_warning("Nessun file cmdline.txt trovato (né /boot/cmdline.txt né /boot/firmware/cmdline.txt)")
            return True
        
        try:
            with open(cmdline_file, 'r') as f:
                content = f.read()
            
            print_info(f"Analisi file: {cmdline_file}")
            
            if 'console=serial0' in content or 'console=ttyS0' in content:
                print_error(f"Console seriale abilitata in {cmdline_file}!")
                print_info(f"Contenuto: {content[:150]}...")
                self.issues.append(f"Console seriale abilitata in {cmdline_file}")
                self.fixes.append(f"Rimuovi 'console=serial0' o 'console=ttyS0' da {cmdline_file} e riavvia")
                return False
            else:
                print_success(f"Console seriale non abilitata in {cmdline_file}")
                return True
                
        except PermissionError:
            print_error(f"Permessi insufficienti per leggere {cmdline_file}")
            return False
        except Exception as e:
            print_error(f"Errore lettura {cmdline_file}: {e}")
            return False
    
    def check_serial_ports(self):
        """Mostra tutte le porte seriali disponibili"""
        print_section("6. Porte seriali disponibili")
        
        try:
            ports = serial.tools.list_ports.comports()
            if ports:
                print_info("Porte seriali trovate:")
                for port in ports:
                    print(f"  - {port.device}: {port.description}")
                    if port.hwid:
                        print(f"    HWID: {port.hwid}")
            else:
                print_warning("Nessuna porta seriale trovata")
        except Exception as e:
            print_warning(f"Errore enumerazione porte: {e}")
    
    def check_dmesg(self):
        """Mostra messaggi dmesg relativi alla porta seriale"""
        print_section("7. Messaggi kernel (dmesg)")
        
        try:
            # Leggi dmesg e filtra per ttyS0 o serial
            dmesg_output = run_command(['dmesg'])
            if dmesg_output:
                lines = dmesg_output.split('\n')
                relevant = [l for l in lines if 'ttyS0' in l.lower() or 'serial' in l.lower() or 'uart' in l.lower()][-10:]
                if relevant:
                    print_info("Ultimi messaggi rilevanti:")
                    for line in relevant:
                        print(f"  {line}")
                else:
                    print_info("Nessun messaggio rilevante trovato in dmesg")
            else:
                print_warning("Impossibile leggere dmesg (potrebbe richiedere permessi root)")
        except Exception as e:
            print_warning(f"Errore lettura dmesg: {e}")
    
    def test_serial_connection(self) -> bool:
        """Testa la connessione seriale"""
        print_section("8. Test connessione seriale")
        
        try:
            print_info(f"Tentativo connessione a {self.port} (baudrate: {self.baudrate})...")
            
            self.serial_conn = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=2.0,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS
            )
            
            if self.serial_conn.is_open:
                print_success(f"Connessione seriale stabilita su {self.port}")
                print_info(f"Impostazioni: {self.baudrate} baud, 8N1")
                return True
            else:
                print_error("Connessione seriale non aperta")
                self.issues.append("Impossibile aprire connessione seriale")
                return False
                
        except serial.SerialException as e:
            error_msg = str(e)
            print_error(f"Errore connessione seriale: {error_msg}")
            
            if "Permission denied" in error_msg or "Operation not permitted" in error_msg:
                self.issues.append("Permessi insufficienti per aprire porta seriale")
                self.fixes.append(f"sudo chmod 666 {self.port}")
                self.fixes.append(f"sudo usermod -a -G dialout $USER")
            elif "could not open port" in error_msg or "No such file" in error_msg:
                self.issues.append("Porta seriale non disponibile")
            else:
                self.issues.append(f"Errore seriale: {error_msg}")
            
            return False
        except Exception as e:
            print_error(f"Errore generico: {e}")
            self.issues.append(f"Errore generico: {e}")
            return False
    
    def test_gps_data(self, duration: int = 10) -> bool:
        """Testa la ricezione dati dal GPS"""
        print_section("9. Test ricezione dati GPS")
        
        if not self.serial_conn or not self.serial_conn.is_open:
            print_error("Connessione seriale non disponibile")
            return False
        
        print_info(f"Lettura dati per {duration} secondi...")
        print_info("(Premi Ctrl+C per interrompere)")
        
        data_received = False
        nmea_sentences = []
        start_time = time.time()
        
        try:
            while time.time() - start_time < duration:
                if self.serial_conn.in_waiting > 0:
                    try:
                        line = self.serial_conn.readline().decode('utf-8', errors='ignore').strip()
                        if line:
                            data_received = True
                            nmea_sentences.append(line)
                            
                            # Mostra le prime frasi NMEA
                            if len(nmea_sentences) <= 5:
                                print(f"  📡 {line}")
                            
                            # Verifica se è una frase NMEA valida
                            if line.startswith('$'):
                                print_success("Frasi NMEA ricevute!")
                    except UnicodeDecodeError:
                        # Dati binari, ignora
                        pass
                    except Exception as e:
                        print_warning(f"Errore lettura: {e}")
                
                time.sleep(0.1)
            
            if data_received:
                print_success(f"Ricevute {len(nmea_sentences)} frasi NMEA")
                
                # Analizza tipi di frasi
                sentence_types = {}
                for sentence in nmea_sentences:
                    if sentence.startswith('$'):
                        parts = sentence[1:].split(',')
                        if parts:
                            sentence_type = parts[0][:6]  # Es: GPGGA, GPRMC
                            sentence_types[sentence_type] = sentence_types.get(sentence_type, 0) + 1
                
                if sentence_types:
                    print_info("Tipi di frasi NMEA ricevute:")
                    for stype, count in sentence_types.items():
                        print(f"  - {stype}: {count}")
                
                return True
            else:
                print_error("Nessun dato ricevuto dal GPS")
                self.issues.append("Nessun dato ricevuto dal GPS")
                
                # Verifica se ci sono dati raw (anche non NMEA)
                try:
                    if self.serial_conn.in_waiting > 0:
                        raw_data = self.serial_conn.read(self.serial_conn.in_waiting)
                        print_warning(f"Dati raw ricevuti ma non decodificabili ({len(raw_data)} bytes)")
                        print_info("Questo potrebbe indicare un baudrate errato")
                        self.fixes.append("Prova baudrate alternativi: 115200, 38400, 19200, 4800")
                except:
                    pass
                
                print_info("Possibili cause e soluzioni:")
                print_info("  1. GPS non alimentato")
                print_info("     → Verifica che il GPS riceva alimentazione (LED acceso?)")
                print_info("  2. Cavi non collegati correttamente")
                print_info("     → Verifica connessioni TX/RX (TX GPS → RX Pi, RX GPS → TX Pi)")
                print_info("     → Verifica massa comune (GND)")
                print_info("  3. GPS in modalità sleep o non inizializzato")
                print_info("     → Il GPS potrebbe richiedere alcuni secondi per avviarsi")
                print_info("     → Prova ad attendere 30-60 secondi all'aperto")
                print_info("  4. Baudrate errato")
                print_info("     → Lo script testerà automaticamente baudrate alternativi")
                print_info("  5. GPS non ha vista del cielo")
                print_info("     → Porta il GPS all'aperto per ottenere segnale")
                print_info("  6. Porta seriale errata")
                print_info("     → Verifica quale porta usa effettivamente il GPS")
                return False
                
        except KeyboardInterrupt:
            print_info("\nTest interrotto dall'utente")
            if data_received:
                print_success(f"Ricevute {len(nmea_sentences)} frasi NMEA prima dell'interruzione")
                return True
            return False
        except Exception as e:
            print_error(f"Errore durante test: {e}")
            return False
    
    def test_different_baudrates(self):
        """Testa diversi baudrate"""
        print_section("10. Test baudrate alternativi")
        
        if not self.serial_conn or not self.serial_conn.is_open:
            print_error("Connessione seriale non disponibile")
            return
        
        baudrates = [9600, 115200, 38400, 19200, 4800]
        print_info(f"Test baudrate: {', '.join(map(str, baudrates))}")
        
        for baud in baudrates:
            try:
                print_info(f"Test {baud} baud...")
                self.serial_conn.baudrate = baud
                time.sleep(0.5)
                
                # Prova a leggere
                if self.serial_conn.in_waiting > 0:
                    line = self.serial_conn.readline().decode('utf-8', errors='ignore').strip()
                    if line and line.startswith('$'):
                        print_success(f"✅ Dati ricevuti a {baud} baud!")
                        print(f"   Esempio: {line[:60]}")
                        return baud
            except Exception as e:
                print_warning(f"Errore test {baud} baud: {e}")
        
        print_warning("Nessun baudrate funzionante trovato")
        return None
    
    def cleanup(self):
        """Chiude la connessione seriale"""
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()
            print_info("Connessione seriale chiusa")
    
    def print_summary(self):
        """Stampa un riepilogo dei problemi trovati"""
        print_header("RIEPILOGO DEBUG")
        
        if not self.issues:
            print_success("Nessun problema rilevato!")
            print_info("Il GPS dovrebbe funzionare correttamente.")
        else:
            print_error(f"Trovati {len(self.issues)} problemi:")
            for i, issue in enumerate(self.issues, 1):
                print(f"  {i}. {issue}")
        
        if self.fixes:
            print_section("SOLUZIONI SUGGERITE")
            for i, fix in enumerate(self.fixes, 1):
                print(f"  {i}. {fix}")
    
    def run_full_debug(self):
        """Esegue tutti i test di debug"""
        print_header("DEBUG GPS L76K")
        print_info(f"Porta: {self.port}")
        print_info(f"Baudrate: {self.baudrate}")
        print_info(f"Utente: {os.getenv('USER', 'unknown')}")
        
        try:
            # Test base
            device_ok = self.check_device_exists()
            if not device_ok:
                print_warning("Dispositivo non trovato, alcuni test verranno saltati")
            
            if device_ok:
                self.check_permissions()
                self.check_processes_using_port()
            
            self.check_uart_config()
            self.check_cmdline_txt()
            self.check_serial_ports()
            self.check_dmesg()
            
            # Test connessione solo se il dispositivo esiste
            if device_ok:
                if self.test_serial_connection():
                    # Test ricezione dati
                    self.test_gps_data(duration=10)
                    
                    # Test baudrate alternativi se non ci sono dati
                    if not self.serial_conn.in_waiting:
                        self.test_different_baudrates()
            
        finally:
            self.cleanup()
            self.print_summary()

def main():
    """Funzione principale"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Debug GPS L76K')
    parser.add_argument(
        '--port',
        type=str,
        default='/dev/ttyS0',
        help='Porta seriale (default: /dev/ttyS0)'
    )
    parser.add_argument(
        '--baudrate',
        type=int,
        default=9600,
        help='Baudrate (default: 9600)'
    )
    parser.add_argument(
        '--duration',
        type=int,
        default=10,
        help='Durata test ricezione dati in secondi (default: 10)'
    )
    
    args = parser.parse_args()
    
    debugger = GPSDebugger(port=args.port, baudrate=args.baudrate)
    
    try:
        debugger.run_full_debug()
    except KeyboardInterrupt:
        print_info("\n\nDebug interrotto dall'utente")
        debugger.cleanup()
        sys.exit(0)
    except Exception as e:
        print_error(f"Errore critico: {e}")
        import traceback
        traceback.print_exc()
        debugger.cleanup()
        sys.exit(1)

if __name__ == "__main__":
    main()

