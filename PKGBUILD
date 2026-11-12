# Paquete de Arch Linux para Luces Navideñas. Se construye desde esta carpeta:
#   make paquete     (pasa el linter y las pruebas, y luego makepkg)
#   makepkg -si      (instala)
pkgname=luces-navidenas
pkgver=1.6
pkgrel=1
pkgdesc="Bombillitos de Navidad en el borde de la pantalla, inspirado en Holiday Lights de Windows 95"
arch=('any')
license=('GPL-3.0-or-later')
depends=('python' 'python-gobject' 'python-cairo' 'gtk3' 'gtk-layer-shell')
optdepends=('pipewire: música y efectos de sonido (pw-cat, pw-play)'
            'ffmpeg: música en MP3/OGG/FLAC'
            'timidity++: villancicos en MIDI'
            'upower: fluidez automática con batería'
            'power-profiles-daemon: fluidez automática en modo ahorro')
source=('luces-navidenas' 'luces-navidenas.desktop' 'luces-navidenas.svg' 'LICENSE')
sha256sums=('SKIP' 'SKIP' 'SKIP' 'SKIP')  # archivos locales de esta misma carpeta

package() {
  install -Dm755 luces-navidenas "$pkgdir/usr/bin/luces-navidenas"
  sed -i "s/^BUILD_DATE = \"@BUILD_DATE@\"/BUILD_DATE = \"$(date '+%Y-%m-%d %H:%M')\"/" "$pkgdir/usr/bin/luces-navidenas"
  install -Dm644 luces-navidenas.desktop "$pkgdir/usr/share/applications/luces-navidenas.desktop"
  install -Dm644 luces-navidenas.svg "$pkgdir/usr/share/icons/hicolor/scalable/apps/luces-navidenas.svg"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
