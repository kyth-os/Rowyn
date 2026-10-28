"""Shared Rowyn wallpaper: artwork, immutable image defaults, and live wiring."""
import base64
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
import yaml

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'shared/kde-wallpaper-defaults.sh'
ASSETS = ROOT / 'shared/wallpaper'
THEMES = ('org.kde.breeze.desktop', 'org.kde.breezedark.desktop')

class WallpaperTests(unittest.TestCase):
    def test_artwork_is_black_with_centered_rowyn_brand_and_4k_image(self):
        svg = ASSETS / 'rowyn-black.svg'
        self.assertTrue(svg.is_file())
        # Version-controlled local artwork only; never parse external SVG input here.
        tree = ET.parse(svg).getroot()
        ns = {'s': 'http://www.w3.org/2000/svg'}
        background = tree.find('s:rect', ns)
        assert background is not None
        self.assertEqual(background.get('fill'), '#000000')
        self.assertIn('ROWYN', ''.join(tree.itertext()))
        png = ASSETS / 'contents/images/3840x2160.png'
        data = png.read_bytes()
        self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack('>II', data[16:24]), (3840, 2160))
        metadata = json.loads((ASSETS / 'metadata.json').read_text())
        self.assertEqual(metadata['KPlugin']['Id'], 'RowynBlack')

    def test_wallpaper_embeds_the_supplied_logo_not_a_generated_monogram(self):
        tree = ET.parse(ASSETS / 'rowyn-black.svg').getroot()
        image = tree.find('{http://www.w3.org/2000/svg}image')
        self.assertIsNotNone(image, 'Use the supplied Rowyn artwork, not the old R monogram')
        assert image is not None
        href = image.get('href', '')
        self.assertTrue(href.startswith('data:image/png;base64,'))
        embedded = base64.b64decode(href.split(',', 1)[1])
        self.assertEqual(embedded, (ASSETS / 'rowyn-logo.png').read_bytes())
        self.assertIsNone(tree.find('{http://www.w3.org/2000/svg}g'))

    def seed(self, root):
        for theme in THEMES:
            path = root / 'usr/share/plasma/look-and-feel' / theme / 'contents/defaults'
            path.parent.mkdir(parents=True)
            path.write_text('[kdeglobals][General]\nColorScheme=BreezeDark\n\n[Wallpaper]\nImage=Next\n\n[kwinrc][org.kde.kdecoration2]\ntheme=Breeze\n')

    def test_helper_sets_both_theme_defaults_and_preserves_user_wallpaper(self):
        with tempfile.TemporaryDirectory(prefix='rowyn-wallpaper-') as temp:
            root = Path(temp)
            self.seed(root)
            user = root / 'var/home/test/.config/plasma-org.kde.plasma.desktop-appletsrc'
            user.parent.mkdir(parents=True)
            user.write_text('Image=file:///home/test/my-chosen-photo.png\n')
            for _ in range(2):
                p = subprocess.run(['bash', str(HELPER), '--root', str(root)], text=True, capture_output=True)
                self.assertEqual(p.returncode, 0, p.stderr)
                for theme in THEMES:
                    data = (root / 'usr/share/plasma/look-and-feel' / theme / 'contents/defaults').read_text()
                    self.assertIn('[Wallpaper]\nImage=RowynBlack', data)
                    self.assertEqual(data.count('Image=RowynBlack'), 1)
                    self.assertIn('ColorScheme=BreezeDark', data)
                    self.assertIn('theme=Breeze', data)
                self.assertEqual(user.read_text(), 'Image=file:///home/test/my-chosen-photo.png\n')
                installed = root / 'usr/share/wallpapers/RowynBlack/contents/images/3840x2160.png'
                self.assertEqual(installed.read_bytes(), (ASSETS / 'contents/images/3840x2160.png').read_bytes())

    def test_published_image_smoke_requires_the_wallpaper_and_selected_default(self):
        workflow = (ROOT / '.github/workflows/build.yml').read_text()
        self.assertIn('test -s /usr/share/wallpapers/RowynBlack/contents/images/3840x2160.png', workflow)
        self.assertIn('--group Wallpaper --key Image)', workflow)
        self.assertIn('= RowynBlack', workflow)

    def test_payload_live_context_and_trigger_all_ship_the_shared_wallpaper(self):
        payload = (ROOT / 'Containerfile').read_text()
        live = (ROOT / 'build/configure-live.sh').read_text()
        context = (ROOT / 'build/custom-installer-iso.sh').read_text()
        self.assertIn('/ctx/shared/kde-wallpaper-defaults.sh --root /', payload)
        self.assertIn('/rowyn/kde-wallpaper-defaults.sh --root /', live)
        self.assertIn('shared/kde-wallpaper-defaults.sh', context)
        self.assertIn('shared/wallpaper', context)
        workflow = yaml.safe_load((ROOT / '.github/workflows/installer-iso.yml').read_text())
        paths = workflow[True]['push']['paths']
        self.assertIn('shared/kde-wallpaper-defaults.sh', paths)
        self.assertIn('shared/wallpaper/**', paths)

if __name__ == '__main__':
    unittest.main()
