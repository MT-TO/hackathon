# Photags

Solution complète du sujet `hackathon-2026.pdf` sous forme d'application web locale en Python.

## Fonctionnalités couvertes

- navigation dans `Images/` avec profondeur maximale de deux niveaux
- génération et mise en cache des miniatures avec taille réglable
- génération et mise en cache des vignettes avec taille réglable
- réglage de la qualité JPEG des variantes mises en cache
- affichage `vignette -> miniature -> original UHD`
- ajout et retrait de tags par lots
- mise en favoris avec filtre dédié
- rotation des images par pas de 90° dans l'interface
- tag automatique local sur une image via Vision macOS, avec priorité au sujet saillant au premier plan
- retrait rapide d'un tag par clic droit sur la pastille
- lecture des données EXIF sur la fiche image quand elles sont disponibles
- création de sous-répertoires
- déplacement d'images par lots
- filtrage par dossier, par tag, et affichage des images sans tag
- stockage des tags dans un fichier JSON, sans base de données

## Arborescence attendue

Déposez vos images dans :

```text
Images/
  2026_02_Londres/
    Chesters/
    Londres/
    Château/
  2026_01_Biaritz/
    Repas/
    Visite_guidée/
```

Le projet génère automatiquement :

```text
.cache/
  metadata.json
  miniatures/
  vignettes/
```

## Lancement

Depuis le dossier du projet :

```bash
python3 app.py
```

Puis ouvrir :

```text
http://127.0.0.1:5001
```

## Notes techniques

- `Flask` est chargé depuis le `venv` local déjà présent sur la machine.
- `Pillow` est utilisé pour produire les miniatures et vignettes.
- Le cache d'index est en mémoire avec invalidation automatique après les actions de l'interface.
