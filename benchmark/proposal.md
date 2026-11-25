Benchmark muss deutlich vereinfacht werden.

Generell
- row number zählt nicht
- ring_no pro dezimal stelle prüfen; einen durchgezogenen Fehler A50401 -> A90401 nicht zu stark bestrafen
- species (3x weight) -> levenstein distanz ist ok
- age exact match
- ll oder " sollen noch post processed werden -> allerdings in csv mit emoji markieren.
- 50% discount falls flasches geflaggt
- 25% abzug falls richtges geflaggt

Werte pro Spalte (optional mit ausgewählten modellen)
- Wo sind die meisten Fehler
- Bei benchmark rohdaten erstmal speichern, bei aufruf damit berechnen und digram erstellen

Werte pro Zelle (optional)
- Welche Zellen werden durch die Bank weg flasch gemacht (evtl. labeling fehler)
- Auch wieder aus Ergebnissen berechnen

Flags
- Precision + Recall for flags
- F Beta mit beta=2 recall is more important

- Nicht mehr die Pipeline steps ausgeben und speichern -> ergebnis als csv

Kosten (avg)
- Kosten pro 100 Page
- Error pro 100 Page -> abs +  prozentuell
- Preis pro 100 korrekten Zellen

H = hallucinated (reference empty), M = missed (prediction empty), F = flagged cells aus altem report behalten

sklearn und levenstein packet nutzen
