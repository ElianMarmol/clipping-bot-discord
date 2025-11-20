import subprocess
import time

# Lanzar los bots en paralelo
processes = [
    subprocess.Popen(["python", "bots/main.py"]),
    subprocess.Popen(["python", "bots/main2.py"]),
    subprocess.Popen(["python", "bots/main3.py"])
]

print("🚀 Los 3 bots fueron iniciados correctamente...")

# Mantener el proceso vivo para Railway
try:
    while True:
        time.sleep(10)
except KeyboardInterrupt:
    print("Cerrando bots...")
    for p in processes:
        p.terminate()