from mqtt_as import MQTTClient
from mqtt_local import config
import uasyncio as asyncio
import dht, machine, ujson

sensor = dht.DHT22(machine.Pin(15))
# led = machine.Pin(6, machine.Pin.OUT)
led = machine.Pin("LED", machine.Pin.OUT)
rele = machine.Pin(9, machine.Pin.OUT, value=1)

estado = {
    "setpoint": 25.0,
    "periodo": 10,       # Segundos entre publicaciones
    "modo": "manual",    # "manual" o "automatico"
    "rele": 0            # 0 = apagado, 1 = encendido (estado lógico, no eléctrico)
}

# Guarda los parámetros persistentes en la memoria flash como JSON
def guardar_estado():
    try:
        # Filtramos solo lo que nos interesa guardar. 
        # Esto evita escribir 'temp_actual' en la memoria flash
        persistente = {
            "setpoint": estado["setpoint"],
            "periodo": estado["periodo"],
            "modo": estado["modo"],
            "rele": estado["rele"]
        }
        # Abrimos en modo escritura de texto ("w")
        with open("estado.json", "w") as f:
            ujson.dump(persistente, f)
    except Exception as e:
        print("Error guardando estado:", e)

# Carga los datos del archivo JSON a la RAM al iniciar
def cargar_estado():
    global estado # Aseguramos que modificamos el diccionario global
    try:
        # Abrimos en modo lectura de texto ("r")
        with open("estado.json", "r") as f:
            estado_guardado = ujson.load(f)
            # Actualizamos nuestro diccionario con los valores recuperados
            estado.update(estado_guardado) 
        print("Estado cargado desde flash:", estado)
    except OSError:
        # Si no existe el archivo (primer encendido), lo creamos con valores por defecto
        print("Archivo de estado no encontrado. Creando uno nuevo...")
        guardar_estado()

# Llamamos a cargar_estado() al momento de correr el script
cargar_estado()

# Hace parpadear el LED durante un tiempo determinado sin detener el resto del programa
async def destellar(segundos=3):
    fin = asyncio.get_event_loop().time() + segundos
    while asyncio.get_event_loop().time() < fin:
        led.value(not led.value()) # Conmuta el estado
        await asyncio.sleep(0.2)
    led.value(0) # Asegurar que quede apagado

# Mide sensores y publica el estado completo en formato JSON
async def medir_y_publicar(client):
    while True:
        try:
            sensor.measure()
            temp = sensor.temperature()
            hum = sensor.humidity()

            # Guardar la medición para que el termostato la vea
            estado["temp_actual"] = temp
            
            # Construimos el objeto JSON según la consigna
            payload = {
                "temperatura": temp,
                "humedad": hum,
                "setpoint": estado["setpoint"],
                "periodo": estado["periodo"],
                "modo": estado["modo"]
            }
            
            print("Publicando:", payload)
            await client.publish("BrianArandaa", ujson.dumps(payload), qos=1)
            
        except OSError:
            print("Error al leer el sensor DHT22")
            
        # Esperamos el tiempo definido en el periodo (dinámico)
        await asyncio.sleep(estado["periodo"])

# Alternativa basada en eventos para procesar mensajes entrantes
async def escuchar_mensajes(client):
    # En mqtt_as, client.queue es un iterador asíncrono de mensajes
    async for topic, msg, retained in client.queue:
        t = topic.decode()
        m = msg.decode()
        print(f"Mensaje recibido en {t}: {m}")
        
        # Lógica para actualizar parámetros
        if "setpoint" in t:
            estado["setpoint"] = float(m)
            guardar_estado()
        elif "periodo" in t:
            estado["periodo"] = int(m)
            guardar_estado()
        elif "modo" in t:
            estado["modo"] = m # "automatico" o "manual"
            guardar_estado()
        elif "destello" in t:
            # Lanzamos la tarea de destello sin esperar a que termine (non-blocking)
            asyncio.create_task(destellar())
        elif "rele" in t and estado["modo"] == "manual":
            estado["rele"] = int(m)
            guardar_estado()
            # El cambio físico del relé se encarga la Tarea de Control

# Evalúa constantemente el estado y actúa sobre el pin físico del relé
async def control_termostato():
    while True:
        modo = estado["modo"]
        
        # Leemos la temperatura actual (asumiendo que la tarea de medición la guardó)
        # Usamos .get() con un valor seguro por si aún no se hizo la primera lectura
        temp_actual = estado.get("temp_actual", estado["setpoint"]) 
        
        if modo == "automatico":
            # Consigna: "El relé se accionará cuando se supere la temperatura de setpoint"
            if temp_actual > estado["setpoint"]:
                rele.value(0) # Recordar: 0 = ACTIVO BAJO (Relé encendido)
                estado["rele"] = 1 # Mantenemos coherencia lógica en nuestro diccionario
            else:
                rele.value(1) # 1 = Apagado físicamente
                estado["rele"] = 0
                
        elif modo == "manual":
            # En modo manual, el pin físico obedece ciegamente al diccionario
            # (El cual fue modificado por la tarea que escucha los mensajes MQTT)
            if estado["rele"] == 1:
                rele.value(0) # Encender
            else:
                rele.value(1) # Apagar
        
        # Evaluar cada 1 
        await asyncio.sleep(1)

# Punto de entrada de las rutinas asíncronas
async def main(client):
    print("Conectando al WiFi y al Broker MQTT...")
    await client.connect()
    print("¡Conectado exitosamente!")
    
    # create_task() las pone en la fila de ejecución
    asyncio.create_task(medir_y_publicar(client))
    asyncio.create_task(escuchar_mensajes(client))
    asyncio.create_task(control_termostato())
    
    while True:
        await asyncio.sleep(60)

async def wifi_han(state):
    print('Wifi is ', 'up' if state else 'down')
    await asyncio.sleep(2)

async def conn_han(client):
    # QoS 1 asegura que el mensaje se entregue al menos una vez
    await client.subscribe(f"BrianArandaa/setpoint", 1)
    await client.subscribe(f"BrianArandaa/periodo", 1)
    await client.subscribe(f"BrianArandaa/destello", 1)
    await client.subscribe(f"BrianArandaa/modo", 1)
    await client.subscribe(f"BrianArandaa/rele", 1)

# Define configuration
# config['subs_cb'] = sub_cb
config['connect_coro'] = conn_han
config['wifi_coro'] = wifi_han
config['ssl'] = True
config['queue_len'] = 1

# Set up client
MQTTClient.DEBUG = True  # Optional
client = MQTTClient(config)
try:
    asyncio.run(main(client))
finally:
    client.close()
    rele.value(1) # Apagamos el relé
    asyncio.new_event_loop()