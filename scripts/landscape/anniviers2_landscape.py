# Val d'Anniviers, batch 2: the south (Zinal, Sorebois) below the Vercorin map
import os
os.environ['VER_BOUNDS'] = '373500,5102000,402500,5110250'
os.environ['VER_PREFIX'] = 'anniviers2'
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vercorin_landscape.py'), encoding='utf-8').read())
