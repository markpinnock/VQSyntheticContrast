import datetime
import json
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import os
from pathlib import Path
import tensorflow as tf
import time

from vq_sce import ABDO_WINDOW

np.set_printoptions(precision=4, suppress=True)


class TrainingLoop:

    def __init__(self,
                 Model: object,
                 dataset: object,
                 train_generator: object,
                 val_generator: object,
                 config: dict):
        self.Model = Model
        self.config = config
        self.EPOCHS = config["expt"]["epochs"]

        expt_path = Path(config["paths"]["expt_path"])
        self.image_save_path = expt_path / "images"
        self.image_save_path.mkdir(parents=True, exist_ok=True)
        self.image_save_path / "train"
        self.model_save_path = expt_path / "models"
        self.log_save_path = expt_path / "logs"
        self.SAVE_EVERY = config["expt"]["save_every"]

        if "scales" not in config["hyperparameters"].keys():
            self.multi_scale = False
        elif len(config["hyperparameters"]["scales"]) == 1:
            self.multi_scale = False
        else:
            self.multi_scale = True

        if not os.path.exists(self.image_save_path / "train"):
            os.mkdir(self.image_save_path / "train")

        if not os.path.exists(self.image_save_path / "validation"):
            os.mkdir(self.image_save_path / "validation")

        self.train_generator = train_generator
        self.val_generator = val_generator
        self.ds_train, self.ds_val = dataset

        log_time = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.train_writer = tf.summary.create_file_writer(str(self.log_save_path / log_time / "train"))
        self.test_writer = tf.summary.create_file_writer(str(self.log_save_path / log_time / "test"))

    def _save_train_results(self, epoch):
        # Log losses
        self.results["train_L1"].append(float(self.Model.L1_metric.result()))
        self.results["train_vq"].append(float(self.Model.vq_metric.result()))
        self.results["train_total"].append(float(self.Model.total_metric.result()))

        if self.config["expt"]["log_scalars"]:
            with self.train_writer.as_default():
                tf.summary.scalar("train_L1", self.Model.L1_metric.result(), step=epoch)
                tf.summary.scalar("train_vq", self.Model.vq_metric.result(), step=epoch)
                tf.summary.scalar("train_total", self.Model.total_metric.result(), step=epoch)
        
        # Log parameter values
        if self.config["expt"]["log_histograms"]:
            with self.train_writer.as_default():
                for v in self.Model.UNet.trainable_variables:
                    tf.summary.histogram(v.name, v, step=epoch)

    def _save_val_results(self, epoch):
        # Log losses
        self.results["val_L1"].append(float(self.Model.L1_metric.result()))
        self.results["val_vq"].append(float(self.Model.vq_metric.result()))
        self.results["val_total"].append(float(self.Model.total_metric.result()))

        if self.config["expt"]["log_scalars"]:
            with self.test_writer.as_default():
                tf.summary.scalar("val_L1", self.Model.L1_metric.result(), step=epoch)
                tf.summary.scalar("val_vq", self.Model.vq_metric.result(), step=epoch)
                tf.summary.scalar("val_total", self.Model.total_metric.result(), step=epoch)

    def _save_model(self):
        self.Model.UNet.save_weights(self.model_save_path / "model.ckpt")

    def train(self, verbose=1):

        """ Main training loop for U-Net """

        self.results = {}
        self.results["train_L1"] = []
        self.results["train_vq"] = []
        self.results["train_total"] = []
        self.results["val_L1"] = []
        self.results["val_vq"] = []
        self.results["val_total"] = []
        self.results["epochs"] = []
        self.results["time"] = 0

        start_time = time.time()

        for epoch in range(self.EPOCHS):
            self.Model.reset_metrics()

            # Run training step for each batch in training data
            for data in self.ds_train:
                self.Model.train_step(**data)

            self._save_train_results(epoch)
            if verbose:
                print(f"Train epoch {epoch + 1}, L1, VQ, Total: {[metric.result().numpy() for metric in self.Model.metrics]}")

            # Validation step if appropriate
            if self.config["data"]["cv_folds"] > 1:
                self.Model.reset_metrics()

                # Run validation step for each batch in validation data
                for data in self.ds_val:
                    self.Model.test_step(**data)

                self._save_val_results(epoch)
                if verbose:
                    print(f"Val epoch {epoch + 1}, L1, VQ, Total: {[metric.result().numpy() for metric in self.Model.metrics]}")

            # Save example images
            if (epoch + 1) % self.SAVE_EVERY == 0:
                if self.multi_scale:
                    self._save_multiscale_images(epoch + 1, phase="train")
                    self._save_multiscale_images(epoch + 1, phase="validation")
                else:
                    self._save_images(epoch + 1, phase="train")
                    self._save_images(epoch + 1, phase="validation")

            # Save model if necessary
            if (epoch + 1) % self.SAVE_EVERY == 0 and self.config["expt"]["save_model"]:
                self._save_model()

        self.results["time"] = (time.time() - start_time) / 3600
        
        if verbose:
            print(f"Time taken: {(time.time() - start_time) / 3600}")

        json.dump(self.results, open(f"{self.log_save_path}/results.json", 'w'), indent=4)

    def _save_images(self, epoch, phase="validation", tuning_path=None):

        """ Saves sample of images """

        if phase == "train":
            data_generator = self.train_generator
        
        elif phase == "validation":
            data_generator = self.val_generator

        data = data_generator.example_images
        source, target, pred = self.Model.example_inference(**data)

        source = data_generator.un_normalise(source)
        target = data_generator.un_normalise(target)
        pred = data_generator.un_normalise(pred)

        _, axs = plt.subplots(target.shape[0], 5)

        for i in range(target.shape[0]):
            axs[i, 0].imshow(source[i, -1, :, :, 0], cmap="bone", **ABDO_WINDOW)
            axs[i, 0].axis("off")
            axs[i, 1].imshow(target[i, -1, :, :, 0], cmap="bone", **ABDO_WINDOW)
            axs[i, 1].axis("off")
            axs[i, 3].imshow(target[i, -1, :, :, 0] - source[i, -1, :, :, 0], norm=mpl.colors.CenteredNorm(), cmap="bwr")
            axs[i, 3].axis("off")
            axs[i, 2].imshow(pred[i, -1, :, :, 0], cmap="bone", **ABDO_WINDOW)
            axs[i, 2].axis("off")
            axs[i, 4].imshow(np.abs(target[i, -1, :, :, 0] - pred[i, -1, :, :, 0]), norm=mpl.colors.CenteredNorm(), cmap="bwr")
            axs[i, 4].axis("off")

        plt.tight_layout()

        if tuning_path:
            plt.savefig(f"{tuning_path}.png", dpi=250)
        else:
            plt.savefig(self.image_save_path / phase / f"{epoch}.png", dpi=250)

        plt.close()

    def _save_multiscale_images(self, epoch, phase="validation", tuning_path=None):

        """ Saves sample of images from multi-scale U-Net """

        if phase == "train":
            data_generator = self.train_generator
        
        elif phase == "validation":
            data_generator = self.val_generator

        data = data_generator.example_images()
        source, target, pred = self.Model.example_inference(**data)

        source = data_generator.un_normalise(source)
        target = data_generator.un_normalise(target)
        for scale in pred.keys():
            pred[scale] = data_generator.un_normalise(pred[scale])

        _, axs = plt.subplots(target.shape[0], 4 + len(pred.keys()))

        for i in range(target.shape[0]):
            axs[i, 0].imshow(source[i, :, :, 11, 0], cmap="gray", vmin=-150, vmax=250)
            axs[i, 0].axis("off")
            for j, img in enumerate(pred.values()):
                axs[i, 1 + j].imshow(img[i, :, :, 11, 0], cmap="gray", vmin=-150, vmax=250)
                axs[i, 1 + j].axis("off")
            axs[i, 2 + j].imshow(target[i, :, :, 11, 0], cmap="gray", vmin=-150, vmax=250)
            axs[i, 2 + j].axis("off")
            axs[i, 3 + j].imshow(target[i, :, :, 11, 0] - source[i, :, :, 11, 0], norm=mpl.colors.CenteredNorm(), cmap="bwr")
            axs[i, 3 + j].axis("off")
            axs[i, 4 + j].imshow(np.abs(target[i, :, :, 11, 0] - list(pred.values())[-1][i, :, :, 11, 0]), norm=mpl.colors.CenteredNorm(), cmap="bwr")
            axs[i, 4 + j].axis("off")

        plt.tight_layout()

        if tuning_path:
            plt.savefig(f"{tuning_path}.png", dpi=250)
        else:
            plt.savefig(self.image_save_path / phase / f"{epoch}.png", dpi=250)

        plt.close()