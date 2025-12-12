#!/usr/bin/env python3
"""
Script per diagnosticare e risolvere problemi SPI
"""

import os
import subprocess
import sys

print("=" * 60)
print("🔧 DIAGNOSTICA E FIX SPI")
print("=" * 60)

# 1. Verifica device SPI
print("\n1️⃣ Verifica device SPI...")
spi_devices = ['/dev/spidev0.0', '/dev/spidev0.1']
found_devices = []
for dev in spi_devices:
    if os.path.exists(dev):
        print(f"   ✅ Trovato: {dev}")
        if os.access(dev, os.R_OK | os.W_OK):
            print(f"      ✅ Accessibile")
            found_devices.append(dev)
        else:
            print(f"      ❌ NON accessibile (permessi mancanti)")
    else:
        print(f"   ❌ NON trovato: {dev}")

if not found_devices:
    print("\n❌ PROBLEMA: Nessun device SPI trovato!")
    print("\n💡 SOLUZIONE:")
    print("   1. Verifica /boot/config.txt contiene: dtparam=spi=on")
    print("   2. Se non c'è, aggiungilo:")
    print("      sudo nano /boot/config.txt")
    print("      Aggiungi questa riga: dtparam=spi=on")
    print("   3. Salva e riavvia: sudo reboot")
    sys.exit(1)

# 2. Verifica configurazione /boot/config.txt o /boot/firmware/config.txt
print("\n2️⃣ Verifica configurazione SPI...")
config_paths = ['/boot/firmware/config.txt', '/boot/config.txt']
config_file = None
config_content = None

for path in config_paths:
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                config_content = f.read()
                config_file = path
                print(f"   ✅ File trovato: {path}")
                break
        except PermissionError:
            print(f"   ⚠️  Permessi insufficienti per leggere {path}")
            print("   💡 Esegui con: sudo python3 fix_spi.py")
        except Exception as e:
            print(f"   ⚠️  Errore lettura {path}: {e}")

if config_file and config_content:
    if 'dtparam=spi=on' in config_content:
        print("   ✅ SPI abilitato: dtparam=spi=on")
    elif 'dtoverlay=spi' in config_content:
        print("   ✅ SPI abilitato: dtoverlay=spi")
    else:
        print("   ❌ SPI NON abilitato in config.txt")
        print(f"\n💡 SOLUZIONE:")
        print(f"   Esegui questi comandi:")
        print(f"   sudo bash -c 'echo \"dtparam=spi=on\" >> {config_file}'")
        print("   sudo reboot")
        sys.exit(1)
else:
    print("   ⚠️  File config.txt non trovato in /boot/firmware/config.txt o /boot/config.txt")
    print("   💡 Verifica manualmente dove si trova il file di configurazione")

# 3. Verifica gruppo spi
print("\n3️⃣ Verifica gruppo spi...")
try:
    result = subprocess.run(['groups'], capture_output=True, text=True)
    if 'spi' in result.stdout:
        print("   ✅ Utente nel gruppo spi")
    else:
        print("   ❌ Utente NON nel gruppo spi")
        print("\n💡 SOLUZIONE:")
        print("   Esegui:")
        print("   sudo usermod -a -G spi $USER")
        print("   Poi esci e rientra nella sessione (logout/login)")
        print("   Oppure: newgrp spi")
except Exception as e:
    print(f"   ⚠️  Errore: {e}")

# 4. Verifica moduli SPI
print("\n4️⃣ Verifica moduli SPI...")
try:
    result = subprocess.run(['lsmod'], capture_output=True, text=True)
    spi_modules = ['spi_bcm2835', 'spi_bcm2835aux']
    found = False
    for module in spi_modules:
        if module in result.stdout:
            print(f"   ✅ Modulo caricato: {module}")
            found = True
    if not found:
        print("   ⚠️  Nessun modulo SPI trovato")
        print("   💡 Potrebbe essere normale su alcuni sistemi")
except Exception as e:
    print(f"   ⚠️  Errore: {e}")

# 5. Test accesso diretto
print("\n5️⃣ Test accesso diretto a /dev/spidev0.0...")
if os.path.exists('/dev/spidev0.0'):
    try:
        with open('/dev/spidev0.0', 'rb') as f:
            print("   ✅ Accessibile in lettura")
        with open('/dev/spidev0.0', 'wb') as f:
            print("   ✅ Accessibile in scrittura")
    except PermissionError:
        print("   ❌ PERMESSI INSUFFICIENTI!")
        print("\n💡 SOLUZIONE:")
        print("   sudo usermod -a -G spi $USER")
        print("   Poi esci e rientra nella sessione")
        sys.exit(1)
    except Exception as e:
        print(f"   ❌ Errore: {e}")
        sys.exit(1)
else:
    print("   ❌ Device non trovato")
    sys.exit(1)

# Riepilogo
print("\n" + "=" * 60)
print("📊 RIEPILOGO")
print("=" * 60)
print("✅ Device SPI: OK")
print("✅ Configurazione: OK")
print("✅ Permessi: OK")
print("\n✅ SPI sembra configurato correttamente!")
print("\n💡 Se il display è ancora nero, verifica:")
print("   - Connessioni hardware (DIN/MOSI, CLK/SCLK)")
print("   - Pin CS, DC, RST collegati correttamente")
print("   - Alimentazione display (VCC, GND)")

