# Legacy scene mappings

These files preserve the distinct artistic cluster-to-scene assignments from the removed v1, v3, and vday predictors.

- `vday_scene_mapping.json` is directly index-compatible with v4 because vday used byte-identical scaler, PCA, and KMeans artefacts.
- `v1_scene_mapping.json` belongs to a different 100-cluster model. It can be tried by cluster number with v4 or v5, but equal indexes do not represent equivalent audio regions.
- `v3_scene_mapping.json` belongs to a different 120-cluster model. Its first 100 entries can be adapted experimentally, while entries 100–119 have no destination in the current 100-cluster predictors.

Copy or transform a mapping into a predictor's active `scene_mapping.json` only as an explicit experiment. Keep the active file at exactly one entry for every cluster produced by that predictor, and validate every referenced scene or fallback before a show.
