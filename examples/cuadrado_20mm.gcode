; OpenLaser Studio - ejemplo de DEMOSTRACION
; Cuadrado de 20 mm a partir del origen de trabajo, modo laser dinamico (M4)
; Potencia ficticia S100 (10 % con $30=1000): valide en su maquina
G90 G21
M5
G0 X10 Y10
M4 S0
G1 X30 Y10 F1200 S100
G1 X30 Y30
G1 X10 Y30
G1 X10 Y10
M5
G0 X0 Y0
