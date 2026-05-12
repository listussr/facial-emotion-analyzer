from __future__ import annotations

import sys

import torch.nn as nn
import torch.nn.functional as F


class CustomEfficientNetB3(nn.Module):
    """
    Кастомная версия EfficientNet-B3.<br>
    Т.к. при обучении были сохранены только веса без архитектуры.
    """

    def __init__(self, num_classes: int = 8, pretrained: bool = True,
                 use_custom_head: bool = True) -> None:
        super().__init__()

        from efficientnet_pytorch import EfficientNet

        if pretrained:
            self.backbone = EfficientNet.from_pretrained('efficientnet-b3')
        else:
            self.backbone = EfficientNet.from_name('efficientnet-b3')

        for param in self.backbone.parameters():
            param.requires_grad = False

        in_features = self.backbone._fc.in_features

        if use_custom_head:
            self.classifier = nn.Sequential(
                nn.Dropout(0.5),
                nn.Linear(in_features, 512),
                nn.ReLU(),
                nn.Dropout(0.4),
                nn.Linear(512, num_classes),
            )
        else:
            self.classifier = nn.Linear(in_features, num_classes)

    def forward(self, x):
        x = self.backbone.extract_features(x)
        x = self.backbone._avg_pooling(x)
        x = x.flatten(start_dim=1)
        if self.backbone._global_params.dropout_rate > 0:
            x = F.dropout(
                x,
                p=self.backbone._global_params.dropout_rate,
                training=self.training,
            )
        x = self.classifier(x)
        return x

    def unfreeze_backbone(self) -> None:
        for param in self.backbone.parameters():
            param.requires_grad = True


def _register_for_pickle(*classes) -> None:
    main_mod = sys.modules.get('__main__')
    if main_mod is None:
        return
    for cls in classes:
        setattr(main_mod, cls.__name__, cls)


_register_for_pickle(CustomEfficientNetB3)
