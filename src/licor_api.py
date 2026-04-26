import requests
from dotenv import load_dotenv

from config import LICOR_TOKEN

load_dotenv()

URL = "https://api.licor.cloud/v2"

headers = {
    "Authorization": f"Bearer {LICOR_TOKEN}",
    "Content-Type": "application/json"
}

#returns a dictionary of devices
def fetch_devices():
  response = requests.get(f"{URL}/devices", headers=headers)
  data = response.json() #api json response as a dictionary
   
  devices = data['devices']
  
  #printing --- can delete after
  '''
  print("device")
  for device in devices:
    print(f"device name: {device['deviceName']}")
    print(f"device serial: {device['deviceSerialNumber']}")
    print("-----------------------------------------------------------")

    for sensor in device['sensors']:
      print(f"measurement type: {sensor['measurementType']}")
      print(f"sensor serial number: {sensor['sensorSerialNumber']}")
      
  print("=============================================================")
  '''
  return devices


#returns a dictionary of sensor data
def fetch_sensor_data(devices, start_time, end_time):
  device_sensors_summary = {}
  
  for device in devices:
    device_sensors_summary[device['deviceSerialNumber']] = {}
    
    for sensor in device['sensors']:
      response = requests.get(f"{URL}/data", headers=headers, params = {
        "deviceSerialNumber": device['deviceSerialNumber'],
        "sensorSerialNumber": sensor['sensorSerialNumber'],
        "startTime": start_time,
        "endTime": end_time          
      })
      
      device_sensors_summary[device['deviceSerialNumber']][sensor['sensorSerialNumber']] = response.json()
  
  return device_sensors_summary