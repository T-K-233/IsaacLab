# Copyright (c) 2022, NVIDIA CORPORATION & AFFILIATES, ETH Zurich, and University of Toronto
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""A class to coordinate groups of visual markers (such as spheres, frames or arrows)
using `UsdGeom.PointInstancer`_ class.

The class :class:`VisualizationMarkers` is used to create a group of visual markers and
visualize them in the viewport. The markers are represented as :class:`UsdGeom.PointInstancer` prims
in the USD stage. The markers are created as prototypes in the :class:`UsdGeom.PointInstancer` prim
and are instanced in the :class:`UsdGeom.PointInstancer` prim. The markers can be visualized by
passing the indices of the marker prototypes and their translations, orientations and scales.
The marker prototypes can be configured with the :class:`VisualizationMarkersCfg` class.

.. _UsdGeom.PointInstancer: https://graphics.pixar.com/usd/dev/api/class_usd_geom_point_instancer.html
"""

from __future__ import annotations

import numpy as np
import torch
from dataclasses import MISSING
from typing import Any

import omni.isaac.core.utils.prims as prim_utils
import omni.isaac.core.utils.stage as stage_utils
import omni.kit.commands
from omni.isaac.core.materials import PreviewSurface
from omni.isaac.core.prims import GeometryPrim
from pxr import Gf, Sdf, UsdGeom, Vt

from omni.isaac.orbit.utils.assets import check_file_path
from omni.isaac.orbit.utils.configclass import configclass
from omni.isaac.orbit.utils.math import convert_quat


@configclass
class VisualizationMarkersCfg:
    """A class to configure a :class:`VisualizationMarkers`."""

    @configclass
    class MarkerCfg:
        """A class to configure a marker prototype prim."""

        visible: bool = True
        """The visibility of the marker. Defaults to True."""
        prim_type: str = MISSING
        """The prim type of the marker.

        This can be any valid USD prim type, such as "Sphere" or "Cone".
        """
        scale: list[float] | None = None
        """The scale of the marker. Defaults to None."""
        color: list[float] | None = None
        """The RGB color of the marker. Defaults to None.

        If not :obj:`None`, the marker will be colored with the given color. The color is
        applied to the material of the marker prim with a preview surface shader that has a
        precedence over any existing material on the prim.
        """
        attributes: dict[str, Any] | None = None
        """The attributes of the marker. Defaults to None."""

    @configclass
    class FileMarkerCfg(MarkerCfg):
        """A class to configure a marker prototype prim from a USD file."""

        prim_type: str = "Xform"
        """The prim type of the marker. Defaults to "Xform"."""
        usd_path: str = MISSING
        """The path to the USD file of the marker."""

    markers: dict[str, MarkerCfg] = MISSING
    """The dictionary of marker configurations.

    The key is the name of the marker, and the value is the configuration of the marker.
    The key is used to identify the marker in the class.
    """


class VisualizationMarkers:
    """A class to coordinate groups of visual markers (loaded from USD).

    This class allows visualization of different UI markers in the scene, such as points and frames.
    The class wraps around the `UsdGeom.PointInstancer`_ for efficient handling of objects
    in the stage via instancing the created marker prototype prims.

    A marker prototype prim is a reusable template prim used for defining variations of objects
    in the scene. For example, a sphere prim can be used as a marker prototype prim to create
    multiple sphere prims in the scene at different locations. Thus, prototype prims are useful
    for creating multiple instances of the same prim in the scene.

    The class parses the configuration to create different the marker prototypes into the stage. Each marker
    prototype prim is created as a child of the :class:`UsdGeom.PointInstancer` prim. The prim path for the
    the marker prim is resolved using the key of the marker in the :attr:`VisualizationMarkersCfg.markers`
    dictionary. The marker prototypes are created using the :meth:`omni.isaac.core.utils.create_prim`
    function, and then then instanced using :class:`UsdGeom.PointInstancer` prim to allow creating multiple
    instances of the marker prims.

    Switching between different marker prototypes is possible by calling the :meth:`visualize` method with
    the prototype indices corresponding to the marker prototype. The prototype indices are based on the order
    in the :attr:`VisualizationMarkersCfg.markers` dictionary. For example, if the dictionary has two markers,
    "marker1" and "marker2", then their prototype indices are 0 and 1 respectively. The prototype indices
    can be passed as a list or array of integers.

    Usage:
        The following snippet shows how to create 24 sphere markers with a radius of 1.0 at random translations
        within the range [-1.0, 1.0]. The first 12 markers will be colored red and the rest will be colored green.

        .. code-block:: python

            from omni.isaac.orbit.markers import VisualizationMarkersCfg, VisualizationMarkers

            # Create the markers configuration
            # This creates two marker prototypes, "marker1" and "marker2" which are spheres with a radius of 1.0.
            # The color of "marker1" is red and the color of "marker2" is green.
            cfg = VisualizationMarkersCfg(
                markers={
                    "marker1": VisualizationMarkersCfg.MarkerCfg(
                        prim_type="Sphere",
                        attributes={"radius": 1.0},
                        color=(1.0, 0.0, 0.0),
                    ),
                    "marker2": VisualizationMarkersCfg.MarkerCfg(
                        prim_type="Sphere",
                        attributes={"radius": 1.0},
                        color=(0.0, 1.0, 0.0),
                }
            )
            # Create the markers instance
            # This will create a UsdGeom.PointInstancer prim at the given path along with the marker prototypes.
            marker = VisualizationMarkers("/World/Visuals/testMarkers", cfg)

            # Set position of the marker
            # -- randomly sample translations between -1.0 and 1.0
            marker_translations = np.random.uniform(-1.0, 1.0, (24, 3))
            # -- this will create 24 markers at the given translations
            # note: the markers will all be `marker1` since the marker indices are not given
            marker.visualize(translations=marker_translations)

            # alter the markers based on their prototypes indices
            # first 12 markers will be marker1 and the rest will be marker2
            # 0 -> marker1, 1 -> marker2
            marker_indices = [0] * 12 + [1] * 12
            # this will change the marker prototypes at the given indices
            # note: the translations of the markers will not be changed from the previous call
            #  since the translations are not given.
            marker.visualize(marker_indices=marker_indices)

            # alter the markers based on their prototypes indices and translations
            marker.visualize(marker_indices=marker_indices, translations=marker_translations)

    .. _UsdGeom.PointInstancer: https://graphics.pixar.com/usd/dev/api/class_usd_geom_point_instancer.html

    """

    def __init__(self, prim_path: str, cfg: VisualizationMarkersCfg):
        """Initialize the class.

        When the class is initialized, the :class:`UsdGeom.PointInstancer` is created into the stage
        and the marker prims are registered into it.

        Args:
            prim_path: The prim path where the PointInstancer will be created.
            cfg: The configuration for the markers.

        Raises:
            ValueError: When no markers are provided in the :obj:`cfg`.
        """
        # get next free path for the prim
        prim_path = stage_utils.get_next_free_path(prim_path)
        # create a new prim
        prim = prim_utils.define_prim(prim_path, "PointInstancer")
        self._instancer_manager = UsdGeom.PointInstancer(prim)
        # store inputs
        self.prim_path = prim_path
        self.cfg = cfg
        # check if any markers is provided
        if len(self.cfg.markers) == 0:
            raise ValueError(f"The `cfg.markers` cannot be empty. Received: {self.cfg.markers}")

        # create a child prim for the marker
        self._add_markers_prototypes(self.cfg.markers)
        # Note: We need to do this the first time to initialize the instancer.
        #   Otherwise, the instancer will not be visible.
        self._instancer_manager.GetProtoIndicesAttr().Set([0])
        self._instancer_manager.GetPositionsAttr().Set([Gf.Vec3f()])
        self._count = 1

    def __str__(self) -> str:
        """Return a string representation of the class.

        Returns:
            A string representation of the class.
        """
        msg = f"VisualizationMarkers(prim_path={self.prim_path})"
        msg += f"\n\tCount: {self.count}"
        msg += f"\n\tNumber of prototypes: {self.num_prototypes}"
        msg += "\n\tMarkers Prototypes:"
        for index, (name, marker) in enumerate(self.cfg.markers.items()):
            msg += f"\n\t\t[Index: {index}]: {name}: {marker.to_dict()}"
        return msg

    """
    Properties.
    """

    @property
    def num_prototypes(self) -> int:
        """The number of marker prototypes available."""
        return len(self.cfg.markers)

    @property
    def count(self) -> int:
        """The total number of marker instances."""
        # TODO: Update this when the USD API is available (Isaac Sim 2023.1)
        # return self._instancer_manager.GetInstanceCount()
        return self._count

    """
    Operations.
    """

    def set_visibility(self, visible: bool):
        """Sets the visibility of the markers.

        The method does this through the USD API.

        Args:
            visible: flag to set the visibility.
        """
        imageable = UsdGeom.Imageable(self._instancer_manager)
        if visible:
            imageable.MakeVisible()
        else:
            imageable.MakeInvisible()

    def is_visible(self) -> bool:
        """Checks the visibility of the markers.

        Returns:
            True if the markers are visible, False otherwise.
        """
        return self._instancer_manager.GetVisibilityAttr().Get() != UsdGeom.Tokens.invisible

    def visualize(
        self,
        translations: np.ndarray | torch.Tensor | None = None,
        orientations: np.ndarray | torch.Tensor | None = None,
        scales: np.ndarray | torch.Tensor | None = None,
        marker_indices: list[int] | np.ndarray | torch.Tensor | None = None,
    ):
        """Update markers in the viewport.

        .. note::
            If the prim `PointInstancer` is hidden in the stage, the function will simply return
            without updating the markers. This helps in unnecessary computation when the markers
            are not visible.

        Whenever updating the markers, the input arrays must have the same number of elements
        in the first dimension. If the number of elements is different, the `UsdGeom.PointInstancer`
        will raise an error complaining about the mismatch.

        Additionally, the function supports dynamic update of the markers. This means that the
        number of markers can change between calls. For example, if you have 24 points that you
        want to visualize, you can pass 24 translations, orientations, and scales. If you want to
        visualize only 12 points, you can pass 12 translations, orientations, and scales. The
        function will automatically update the number of markers in the scene.

        The function will also update the marker prototypes based on their prototype indices. For instance,
        if you have two marker prototypes, and you pass the following marker indices: [0, 1, 0, 1], the function
        will update the first and third markers with the first prototype, and the second and fourth markers
        with the second prototype. This is useful when you want to visualize different markers in the same
        scene. The list of marker indices must have the same number of elements as the translations, orientations,
        or scales. If the number of elements is different, the function will raise an error.

        .. caution::
            This function will update all the markers instanced from the prototypes. That means
            if you have 24 markers, you will need to pass 24 translations, orientations, and scales.

            If you want to update only a subset of the markers, you will need to handle the indices
            yourself and pass the complete arrays to this function.

        Args:
            translations: Translations w.r.t. parent prim frame. Shape is (M, 3).
                Defaults to :obj:`None`, which means left unchanged.
            orientations: Quaternion orientations (w, x, y, z) w.r.t. parent prim frame. Shape is (M, 4).
                Defaults to :obj:`None`, which means left unchanged.
            scales: Scale applied before any rotation is applied. Shape is (M, 3).
                Defaults to :obj:`None`, which means left unchanged.
            marker_indices: Decides which marker prototype to visualize. Shape is (M).
                Defaults to :obj:`None`, which means left unchanged provided that the total number of markers
                is the same as the previous call. If the number of markers is different, the function
                will update the number of markers in the scene.

        Raises:
            ValueError: When input arrays do not follow the expected shapes.
            ValueError: When the function is called with all :obj:`None` arguments.
        """
        # check if it is visible (if not then let's not waste time)
        if not self.is_visible():
            return
        # check if we have any markers to visualize
        num_markers = 0
        # resolve inputs
        # -- position
        if translations is not None:
            if isinstance(translations, torch.Tensor):
                translations = translations.detach().cpu().numpy()
            # check that shape is correct
            if translations.shape[1] != 3 or len(translations.shape) != 2:
                raise ValueError(f"Expected `translations` to have shape (M, 3). Received: {translations.shape}.")
            # apply translations
            self._instancer_manager.GetPositionsAttr().Set(Vt.Vec3fArray.FromNumpy(translations))
            # update number of markers
            num_markers = translations.shape[0]
        # -- orientation
        if orientations is not None:
            if isinstance(orientations, torch.Tensor):
                orientations = orientations.detach().cpu().numpy()
            # check that shape is correct
            if orientations.shape[1] != 4 or len(orientations.shape) != 2:
                raise ValueError(f"Expected `orientations` to have shape (M, 4). Received: {orientations.shape}.")
            # roll orientations from (w, x, y, z) to (x, y, z, w)
            # internally USD expects (x, y, z, w)
            orientations = convert_quat(orientations, to="xyzw")
            # apply orientations
            self._instancer_manager.GetOrientationsAttr().Set(Vt.QuathArray.FromNumpy(orientations))
            # update number of markers
            num_markers = orientations.shape[0]
        # -- scales
        if scales is not None:
            if isinstance(scales, torch.Tensor):
                scales = scales.detach().cpu().numpy()
            # check that shape is correct
            if scales.shape[1] != 3 or len(scales.shape) != 2:
                raise ValueError(f"Expected `scales` to have shape (M, 3). Received: {scales.shape}.")
            # apply scales
            self._instancer_manager.GetScalesAttr().Set(Vt.Vec3fArray.FromNumpy(scales))
            # update number of markers
            num_markers = scales.shape[0]
        # -- status
        if marker_indices is not None or num_markers != self._count:
            # apply marker indices
            if marker_indices is not None:
                if isinstance(marker_indices, torch.Tensor):
                    marker_indices = marker_indices.detach().cpu().numpy()
                elif isinstance(marker_indices, list):
                    marker_indices = np.array(marker_indices)
                # check that shape is correct
                if len(marker_indices.shape) != 1:
                    raise ValueError(f"Expected `marker_indices` to have shape (M,). Received: {marker_indices.shape}.")
                # apply proto indices
                self._instancer_manager.GetProtoIndicesAttr().Set(Vt.IntArray.FromNumpy(marker_indices))
                # update number of markers
                num_markers = marker_indices.shape[0]
            else:
                # check that number of markers is not zero
                if num_markers == 0:
                    raise ValueError("Number of markers cannot be zero! Hint: The function was called with no inputs?")
                # set all markers to be the first prototype
                self._instancer_manager.GetProtoIndicesAttr().Set([0] * num_markers)
        # set number of markers
        self._count = num_markers

    """
    Helper functions.
    """

    def _add_markers_prototypes(self, markers_cfg: dict[str, VisualizationMarkersCfg.MarkerCfg]):
        """Adds markers prototypes to the scene and sets the markers instancer to use them."""
        # add markers based on config
        for name, cfg in markers_cfg.items():
            # handle basic marker config
            # if it is a file marker, check if the file exists
            # if it is a prim marker, check that prim type is not Xform since that is an empty prim!
            if not isinstance(cfg, VisualizationMarkersCfg.FileMarkerCfg):
                # check if prim type is valid
                if cfg.prim_type == "Xform":
                    raise ValueError("Please use `FileMarkerCfg` for `prim_type` of `Xform`.")
                # set usd path as None
                usd_path = None
            else:
                # check if the file exists
                if not check_file_path(cfg.usd_path):
                    raise FileNotFoundError(f"USD file for the marker not found at: {cfg.usd_path}")
                # make sure user doesn't override the prim type
                if cfg.prim_type != "Xform":
                    raise ValueError(
                        f"Please use `prim_type` of `Xform` for `FileMarkerCfg`. Received: {cfg.prim_type}."
                    )
                # set usd path
                usd_path = cfg.usd_path
            # resolve prim path
            marker_prim_path = f"{self.prim_path}/{name}"
            # create a child prim for the marker
            prim = prim_utils.create_prim(
                prim_path=marker_prim_path,
                prim_type=cfg.prim_type,
                usd_path=usd_path,
                scale=cfg.scale,
                attributes=cfg.attributes,
            )
            # make marker invisible to secondary rays
            omni.kit.commands.execute(
                "ChangePropertyCommand",
                prop_path=Sdf.Path(f"{marker_prim_path}.primvars:invisibleToSecondaryRays"),
                value=True,
                prev=None,
                type_to_create_if_not_exist=Sdf.ValueTypeNames.Bool,
            )
            # set visibility
            prim_utils.set_prim_visibility(prim, visible=cfg.visible)
            # create color attribute
            if cfg.color is not None:
                geom_prim = GeometryPrim(marker_prim_path)
                material = PreviewSurface(f"{marker_prim_path}/material", color=np.asarray(cfg.color))
                geom_prim.apply_visual_material(material, weaker_than_descendants=False)
            # add child reference to point instancer
            self._instancer_manager.GetPrototypesRel().AddTarget(marker_prim_path)
