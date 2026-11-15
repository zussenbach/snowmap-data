# Ski amadé, part 2 of 4 (see skiamade_landscape.py)
import os
os.environ['PART'] = '2'
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'skiamade_landscape.py'), encoding='utf-8').read())
