import numpy as np
import tensorflow as tf

from .layers.conv_layers import DownBlock, UpBlock, VQBlock

MAX_CHANNELS = 512


class UNet(tf.keras.Model):

    """ Input:
        - initialiser e.g. keras.initializers.RandomNormal
        - nc: number of channels in first layer
        - num_layers: number of layers
        - img_dims: input image size
        Returns:
        - keras.Model """

    def __init__(
        self,
        initialiser: tf.keras.initializers.Initializer,
        config: dict,
        name: str | None = None
    ) -> None:

        super().__init__(name=name)

        # Check network and image dimensions
        self._source_dims = tuple(config["source_dims"])
        self._target_dims = tuple(config["target_dims"])
        assert len(self._source_dims) == 3, "3D input only"
        self._config = config
        self._upsample_layer = config["upsample_layer"]
        self._residual = config["residual"]

        if config["vq_layers"] is not None:
            self._vq_layers = config["vq_layers"].keys()
            self._vq_config = {"vq_beta": config["vq_beta"]}
        else:
           self._vq_layers = []
           self._vq_config = None

        self._initialiser = initialiser
        max_num_layers = int(np.log2(np.min([self._source_dims[1], self._source_dims[2]])))
        assert config["layers"] <= max_num_layers and config["layers"] >= 0, (
            f"Maximum number of generator layers: {max_num_layers}"
        )

        self.encoder, self.decoder = [], []
        cache = self.get_encoder()
        self.get_decoder(cache)

    def get_encoder(self) -> dict[str, int | tuple[int]]:
        """" Create U-Net encoder """

        # Cache channels, strides and weights
        cache = {"channels": [], "strides": [], "kernels": [], "upsamp_factor": []}
        source_dims = self._source_dims
        target_dims = self._target_dims

        for i in range(0, self._config["layers"]):
            channels = np.min([self._config["nc"] * 2 ** i, MAX_CHANNELS])

            if (source_dims[0] // 2) < 2:
                source_strides = (2, 2, 2)
                source_kernel = (4, 4, 4)
                source_dims = (
                    source_dims[0] // 2,
                    source_dims[1] // 2,
                    source_dims[2] // 2
                )
            else:
                source_strides = (1, 2, 2)
                source_kernel = (2, 4, 4)
                source_dims = (
                    source_dims[0],
                    source_dims[1] // 2,
                    source_dims[2] // 2
                )

            if (target_dims[0] // 2) < 2:
                target_strides = (2, 2, 2)
                target_kernel = (4, 4, 4)
                target_dims = (
                    target_dims[0] // 2,
                    target_dims[1] // 2,
                    target_dims[2] // 2
                )
            else:
                target_strides = (1, 2, 2)
                target_kernel = (2, 4, 4)
                target_dims = (
                    target_dims[0],
                    target_dims[1] // 2,
                    target_dims[2] // 2
                )

            cache["channels"].append(channels)
            cache["strides"].append(target_strides)
            cache["kernels"].append(target_kernel)
            cache["upsamp_factor"].append(target_dims[0] // source_dims[0])

        for i in range(0, self._config["layers"]):
            use_vq = f"down_{i}" in self._vq_layers
            if use_vq:
                self._vq_config["embeddings"] = self._config["vq_layers"][f"down_{i}"]

            self.encoder.append(
                DownBlock(
                    channels,
                    source_kernel,
                    source_strides,
                    initialiser=self._initialiser,
                    use_vq=use_vq,
                    vq_config=self._vq_config,
                    name=f"down_{i}")
                )

        use_vq = "bottom" in self._vq_layers
        if use_vq:
            self._vq_config["embeddings"] = self._config["vq_layers"]["bottom"]

        self.bottom_layer = DownBlock(
            channels,
            source_kernel,
            (1, 1, 1),
            initialiser=self._initialiser,
            use_vq=use_vq,
            vq_config=self._vq_config,
            name="bottom"
        )

        return cache

    def get_decoder(self, cache: dict[str, int | tuple[int]]) -> None:
        """ Create U-Net decoder """

        for i in range(self._config["layers"] - 1, -1, -1):
            channels = cache["channels"][i]
            strides = cache["strides"][i]
            kernel = cache["kernels"][i]
            upsamp_factor = cache["upsamp_factor"][i]

            use_vq = f"up_{i}" in self._vq_layers
            if use_vq:
                self._vq_config["embeddings"] = self._config["vq_layers"][f"up_{i}"]

            self.decoder.append(
                UpBlock(
                    channels,
                    kernel,
                    strides,
                    upsamp_factor=upsamp_factor,
                    initialiser=self._initialiser,
                    use_vq=use_vq,
                    vq_config=self._vq_config,
                    name=f"up_{i}")
                )

        if self._upsample_layer:
            use_vq = "upsamp" in self._vq_layers
            if use_vq:
                self._vq_config["embeddings"] = self._config["vq_layers"]["upsamp"]

            self.upsample_in = tf.keras.layers.UpSampling3D(size=(1, 2, 2))
            self.upsample_out = UpBlock(
                channels,
                (2, 4, 4),
                (1, 2, 2),
                upsamp_factor=1,
                initialiser=self._initialiser,
                use_vq=use_vq,
                vq_config=self._vq_config,
                name=f"upsamp"
            )

        self.final_layer = tf.keras.layers.Conv3D(
            1, (1, 1, 1), (1, 1, 1),
            padding="same",
            activation="tanh",
            kernel_initializer=self._initialiser,
            name="final"
        )

        if "final" in self._vq_layers:
            self.output_vq = VQBlock(
                num_embeddings=self._vq_config["embeddings"],
                embedding_dim=1,
                beta=self._vq_config["vq_beta"],
                name="output_vq"
            )
        else:
            self.output_vq = None

    def call(self, x: tf.Tensor) -> tuple[tf.Tensor, tf.Tensor | None]:
        skip_layers = []

        if self._upsample_layer:
            upsampled_x = self.upsample_in(x)
        else:
            upsampled_x = x

        for layer in self.encoder:
            x, skip = layer(x, training=True)
            skip_layers.append(skip)

        x, _ = self.bottom_layer(x, training=True)
        skip_layers.reverse()

        for skip, tconv in zip(skip_layers, self.decoder):
            x = tconv(x, skip, training=True)

        if self._upsample_layer:
            x = self.upsample_out(x, upsampled_x)

        x = self.final_layer(x, training=True)

        if self.output_vq is None and not self._residual:
            return x, None

        elif self.output_vq is None and self._residual:
            return x + upsampled_x, None

        elif self.output_vq is not None and not self._residual:
            return x, self.output_vq(x) + upsampled_x

        else:
            return x + upsampled_x, self.output_vq(x) + upsampled_x
