"""Functions for training an object detection model."""

import json
import logging
import math
import os
import subprocess
import sys

import fiftyone as fo
from google.protobuf import text_format
from object_detection.protos.string_int_label_map_pb2 import (
    StringIntLabelMap,
    StringIntLabelMapItem,
)
from object_detection.utils import label_map_util


def train_detection_model(training_name, chosen_model):
    """Train an object detection model.

    Args:
        training_name (str) - user-selected name for model

    Returns:
        ...
    """

    # TODO: cover corner case where user restarts training

    # enforce unique model name
    if os.path.isdir("/tf/ml-model/dataset-export/" + training_name):
        logging.error("Must use unique model name.")
        sys.exit(1)

    base_models = load_base_models_json()

    set_filenames(base_models, training_name, chosen_model)


def load_base_models_json(filename="base_models.json"):
    """Load base models json to allow selecting pre-trained model.

    Args:
        filename (str) - filename for the json file with pre-trained models

    Returns:
        base_models - python dict version of JSON key-value pairs
    """
    with open(filename) as json_file:
        base_models = json.load(json_file)

    return base_models


def set_filenames(base_models, training_name, chosen_model):
    """Set filename values needed for object detection training.

    Args:
        base_models (dict) - possible pre-trained models
        training_name (str) - user-selected name for model
        chosen_model (str) - the user-selected pre-trained model

    Returns:
        filepaths (dict) - keys are names, values are filenames
    """
    filepaths = {}

    # intermediate variables needed later for filename construction
    base_pipeline_file = base_models[chosen_model]["base_pipeline_file"]
    model_name = base_models[chosen_model]["model_name"]

    # set all filepath key-value pairs
    filepaths["train_record_file"] = (
        "/tf/dataset-export/" + training_name + "/train/tf.records"
    )
    filepaths["val_record_file"] = (
        "/tf/dataset-export/" + training_name + "/val/tf.records"
    )
    filepaths["val_export_dir"] = "/tf/dataset-export/" + training_name + "/val/"
    filepaths["train_export_dir"] = "/tf/dataset-export/" + training_name + "/train/"
    filepaths["model_export_dir"] = "/tf/model-export/" + training_name + "/"
    filepaths["label_map_file"] = (
        "/tf/dataset-export/" + training_name + "/label_map.pbtxt"
    )
    filepaths["model_dir"] = "/tf/training/" + training_name + "/"
    filepaths["pretrained_checkpoint"] = base_models[chosen_model][
        "pretrained_checkpoint"
    ]
    filepaths["pipeline_file"] = "/tf/models/research/deploy/" + base_pipeline_file
    filepaths["fine_tune_checkpoint"] = (
        "/tf/models/research/deploy/" + model_name + "/checkpoint/ckpt-0"
    )
    filepaths["base_pipeline_file"] = base_pipeline_file
    # TODO: Return to this later. Is this a bug or not? Will find out later when
    # I get further into this module.
    # filepaths["pipeline_file"] = "/tf/models/research/deploy/pipeline_file.config"

    return filepaths


def export_voxel51_dataset_to_tfrecords(
    dataset_name, filepaths, label_field, training_percentage=0.8
):
    """Export the voxel51 dataset to TensorFlow records.

    Args:
        dataset_name (str) - voxel51 dataset name
        filepaths (dict) - filename values created by set_filenames
        label_field (str) - label field set in config
        training_percentage (float) - percentage of sample for training

    Returns:
        None
    """
    # load voxel51 dataset and create a view
    dataset = fo.load_dataset(dataset_name)
    view = dataset.match_tags("training").shuffle(seed=51)

    # calculate size of training and validation set
    sample_len = len(view)
    train_len = math.floor(sample_len * training_percentage)
    val_len = math.floor(sample_len * (1 - training_percentage))

    # extract training and validation records
    val_view = view.take(val_len)
    train_view = view.skip(val_len).take(train_len)

    # Export the validation and training datasets
    val_view.export(
        export_dir=filepaths["val_export_dir"],
        dataset_type=fo.types.TFObjectDetectionDataset,
        label_field=label_field,
    )
    train_view.export(
        export_dir=filepaths["train_export_dir"],
        dataset_type=fo.types.TFObjectDetectionDataset,
        label_field=label_field,
    )


def create_detection_mapping(dataset_name, label_field):
    """Create mapping from labels to IDs.

    This function creates a mapping necessary for tensorflow
    object detection.

    Args:
        dataset_name (str) - voxel51 dataset name
        label_field (str) - label field set in config

    Returns:
        mapping (str) - mapping of labels to IDs
    """
    # pylint: disable=invalid-name,no-member,redefined-builtin

    logging.info("Creating detection classes to ID mapping.")

    # load voxel51 dataset and create a view
    dataset = fo.load_dataset(dataset_name)
    view = dataset.match_tags("training").shuffle(seed=2021)

    # create a list of all class names
    class_names = _create_list_of_class_names(view, label_field)

    # convert list of class names to a mapping data structure
    # this mapping data structure uses a name as one field and a unique
    # integer id in the other field. It helps the model map a string label
    # name to an id number.
    # for detailed info, see here:
    # https://github.com/tensorflow/models/blob/master/research/object_detection/protos/string_int_label_map.proto
    msg = StringIntLabelMap()
    for id, name in enumerate(class_names, start=1):  # start counting at 1, not 0
        msg.item.append(StringIntLabelMapItem(id=id, name=name))
    mapping = str(text_format.MessageToBytes(msg, as_utf8=True), "utf-8")
    logging.info("Finished creating detection classes to ID mapping.")

    return mapping


def _create_list_of_class_names(view, label_field):
    """Create list of class names from the label field.

    Args:
        view (voxel51 view object) - the voxel51 dataset
        label_field (str) - label field set in config

    Returns:
        class_names (list)
    """
    logging.info("Extracting class names from label field.")
    class_names = []
    for sample in view.select_fields(label_field):
        if sample[label_field] is not None:
            for detection in sample[label_field].detections:
                label = detection["label"]
                if label not in class_names:
                    class_names.append(label)
    logging.info("Finished extracting class names from label field.")
    return class_names


def save_mapping_to_file(mapping, filepaths):
    """Save detection classes to ID mapping file.

    Args:
        mapping - the mapping to save
        filepaths (dict) - filename values created by set_filenames

    """
    logging.info("Creating detection classes to ID mapping file.")
    with open(filepaths["label_map_file"], "w") as f:
        f.write(mapping)
    logging.info("Finished creating detection classes to ID mapping file.")


def get_num_classes_from_label_map(filepaths):
    """Retrieve number of classes from label map file.

    Args:
        mapping_filename (str)

    Returns:
        num_classes (int)
    """
    logging.info("Calculating number of classes in label map file.")
    label_map = label_map_util.load_labelmap(filepaths["label_map_file"])
    categories = label_map_util.convert_label_map_to_categories(
        label_map, max_num_classes=90, use_display_name=True
    )
    category_index = label_map_util.create_category_index(categories)
    num_classes = len(category_index.keys())
    logging.info("Finished calculating number of classes in label map file.")
    return num_classes

  
def download_base_training_config(filepaths):
    """Download base training configuration file.

    Args:
        filepaths (dict) - filename values created by set_filenames

    Returns:
        None
    """
    # pylint: disable=line-too-long
    logging.info("Downloading base training configuration file.")
    # specify configuration file URL
    config_file_url = (
        "https://raw.githubusercontent.com/tensorflow/models/master/research/object_detection/configs/tf2/"
        + filepaths["base_pipeline_file"]
    )

    # run bash script to keep using same commands as jupyter notebook taken
    # from Google. This bash script could be implemented in Python.
    
    # TODO: capture the return value from the bash script. If the wget command
    # fails then abort the script. Or add an asert that kills the script if
    # the excpected file is not there.
    subprocess.run(
        "./install_base_training_config.sh {}".format(config_file_url).split(),
        check=True,
    )

    logging.info("Finished downloading base training configuration file.")
