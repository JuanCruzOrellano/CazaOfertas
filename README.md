# CazaOfertas

App de escritorio para Windows que sigue los precios de tus marcas favoritas y te avisa cuando algo baja y entra en tu rango.

## Descargar

👉 **[Descargar el instalador de CazaOfertas](https://github.com/JuanCruzOrellano/CazaOfertas/releases/latest/download/CazaOfertas-Setup.exe)**

1. Abrí el instalador (no pide permisos de administrador). Si Windows muestra *"Windows protegió su PC"*: **Más información → Ejecutar de todas formas**.
2. Queda el ícono de CazaOfertas en el Escritorio y en el menú Inicio.
3. Si tu antivirus la frena, agregá como excepción la carpeta `%LOCALAPPDATA%\Programs\CazaOfertas`.

La app se actualiza sola: cuando hay una versión nueva aparece un aviso para instalarla.
Tus marcas y precios se guardan en `%APPDATA%\CazaOfertas` y no se pierden al actualizar.

## Cómo se usa

- **+ Agregar marca**: poné la tienda oficial (ej. `nike.com.ar`) y tu precio máximo.
- **Casillas**: lo que ves en *En tu rango*.
- **Campanitas**: de qué te avisa, cada una con su precio.
- **Ajustes (⚙)**: tus talles, revisión automática (y modo evento cada 15 min), avisos por Telegram y tiendas para comparar.
- **Tocá un producto** para ver su historial de precio, tus talles, cuotas, compararlo en otras tiendas, seguirlo o compartirlo.
- **⭐ Seguidos**: productos puntuales con precio objetivo.

Funciona con tiendas VTEX (nike.com.ar, topper.com.ar y muchas tiendas argentinas) y Shopify.

## Publicar una versión nueva (para el dueño del repo)

Subir los cambios y crear una etiqueta `vX.Y.Z` (ej. `v1.0.1`). GitHub arma el .exe y lo publica solo; las apps instaladas lo detectan y ofrecen actualizar.
