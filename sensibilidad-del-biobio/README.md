# Sensibilidad del Biobío

Scripts de la campaña de sensibilidad hidráulica del río Biobío. La configuración oficial evalúa cuatro espaciamientos con los seis perfiles del modelo definitivo y conserva los Levees en sus secciones originales.

## Uso local

1. En Windows, instala Python 3 y HEC-RAS 6.6 con su controlador COM.
2. Desde esta carpeta ejecuta `py -3 -m venv .venv` y `.venv\Scripts\python.exe -m pip install -r requirements.txt`.
3. Copia los archivos oficiales MNMFNuevo.prj, MNMFNuevo.g01, MNMFNuevo.f01, MNMFNuevo.p01 y su HDF de referencia a `modelo_fuente/`, o indica la ruta al proyecto en `CONFIGURACION_AUTOMATICA.json`. La geometría debe coincidir con el SHA-256 configurado.
4. Abre `EJECUTAR_BIOBIO.bat`. Usa la opción 1 para verificar el modelo. La opción 7 inicia las tareas de la campaña, incluidos los cálculos pendientes.
5. Abre `ULTIMO_INFORME.html` para ver el estado y los vínculos a los análisis.

Los datos del modelo, HDF y salidas se mantienen en el entorno local. Este directorio contiene código y configuración; las geometrías históricas y las plantillas nativas de pilotos se conservan en la instalación de origen.
