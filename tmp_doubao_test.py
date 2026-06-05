from PIL import Image
from dotenv import load_dotenv
import image_gen

load_dotenv()
img = Image.new('RGB', (64, 64), (255, 255, 255))
try:
    u = image_gen._request_doubao_image('modern interior background for advertising poster', img, '2K')
    print('OK', u)
except Exception as e:
    print('ERROR', type(e).__name__, str(e))
