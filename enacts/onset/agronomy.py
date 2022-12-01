import xarray as xr
import numpy as np


def soil_plant_water_step(
    sm_yesterday,
    peffective,
    et,
    taw,
):
    """Compute soil-plant-water balance from yesterday to today.
    The balance is thought as a bucket with water coming in and out:
    
    `sm` (t) + `drainage` (t) = `sm` (t-1) + `peffective` (t) - `et` (t)
    
    where:
    
    `sm` is the soil moisture and can not exceed total available water `taw`.
    
    `drainage` is the residual soil moisture occasionally exceeding `taw`
    that drains through the soil.
    
    `peffective` is the effective precipitation that enters the soil.
    
    `et` is the evapotranspiration yielded by the plant.
    
    Parameters
    ----------
    sm_yesterday : DataArray
        soil moisture of yesterday.
    peffective : DataArray
        effective precipitation today.
    et : DataArray
        evapotranspiration of the plant today.
    taw : DataArray
        total available water that represents the maximum water capacity of the soil.
        
    Returns
    -------
    sm, drainage : Tuple of DataArray
        today soil moisture and drainage
        
    See Also
    --------
    soil_plant_water_balance
    
    """
    
    # Water Balance
    wb = (sm_yesterday + peffective - et).clip(min=0)
    drainage = (wb - taw).clip(min=0)
    sm = wb - drainage
    return sm, drainage


def soil_plant_water_balance(
    peffective,
    et,
    taw,
    sminit,
    kc_params=None,
    planting_date=None,
    sm_threshold=None,
    time_dim="T",
):
    """Compute soil-plant-water balance day after day over a growing season.
    See `soil_plant_water_step` for the step by step algorithm definition.
    
    Parameters
    ----------
    peffective : DataArray
        daily effective precipitation.
    et : DataArray
        daily evapotranspiration of the plant.
    taw : DataArray
        total available water that represents the maximum water capacity of the soil.
    sminit : DataArray
        timeless soil moisture to initialize the loop with.
    kc_params : DataArray
        Crop Cultivar Kc parameters as a function of the inflection points of the Kc curve,
        expressed in consecutive daily time deltas originating from `planting_date`
        as coordinate `kc_periods` (default `kc_params` =None in which case Kc is set to 1).
    planting_date : DataArray
        dates when planting (default `planting_date` =None -- not covered yet)
    time_dim : str, optional
        daily time dimension to run the balance against (default `time_dim` ="T").
        
    Returns
    -------
    sm, drainage, et_crop : Tuple of DataArray
        daily soil moisture, drainage and crop evapotranspiration over the growing season.
        
    See Also
    --------
    soil_plant_water_step
    
    Notes
    -----
    The daily evapotranspiration `et` can be scaled by a Crop Cultivar Kc
    modelizing a crop needs in water according to the stage of its growth.
    Kc is set to 1 outside of the growing period. i.e. before planting date
    and after the last Kc curve inflection point.
    
    Examples
    --------
    Example of kc_params:
    
    >>> kc_periods = pd.TimedeltaIndex([0, 45, 47, 45, 45], unit="D")
    >>> kc_params = xr.DataArray(
    >>>    data=[0.2, 0.4, 1.2, 1.2, 0.6], dims=["kc_periods"], coords=[kc_periods]
    >>> )
    <xarray.DataArray (kc_periods: 5)>
    array([0.2, 0.4, 1.2, 1.2, 0.6])
    Coordinates:
        * kc_periods  (kc_periods) timedelta64[ns] 0 days 45 days ... 45 days 45 days
    
    Example of planting_date:
    
    >>> p_d = xr.DataArray(
    >>>    pd.DatetimeIndex(data=["2000-05-02", "2000-05-13"]),
    >>>    dims=["station"],
    >>>    coords={"station": [0, 1]},
    >>> )
    <xarray.DataArray (station: 2)>
    array(['2000-05-02T00:00:00.000000000', '2000-05-13T00:00:00.000000000'],
        dtype='datetime64[ns]')
    Coordinates:
        * station        (station) int64 0 1
    """
    
    # First Step
    if np.size(et) == 1:
        et = xr.DataArray(et)
    if np.size(sminit) == 1:
        sminit = xr.DataArray(sminit)
    # Setting Kc
    if kc_params is None:
        kc = 1
    else:
        kc_inflex = kc_params.assign_coords(
            kc_periods=kc_params["kc_periods"].cumsum(dim="kc_periods")
        )
        if planting_date is not None:
            planted_since = peffective[time_dim][0].drop_vars(time_dim) - planting_date
        else:
            if sm_threshold is not None:
                planted_since = xr.where(
                    sminit >= sm_threshold, 0, np.nan
                ).astype("timedelta64[D]")
            else:
                raise Exception("if planting_date is None, then define a sm_threshold")
        kc = kc_inflex.interp(
                kc_periods=planted_since, kwargs={"fill_value": 1}
            ).where(lambda x: np.isnan(x) == False, other=1).drop_vars("kc_periods")
    # Initializations of sm, drainage and et_crop
    et_crop0 = kc * et.isel({time_dim: 0}, missing_dims='ignore', drop=True)
    sm0, drainage0 = soil_plant_water_step(
        sminit,
        peffective.isel({time_dim: 0}, drop=True),
        et_crop0,
        taw,
    )
    # Give time dimension to sm, drainage and et_crop
    sm = sm0.expand_dims({time_dim: peffective[time_dim]}).copy()
    drainage = drainage0.expand_dims({time_dim: peffective[time_dim]}).copy()
    et_crop = et_crop0.expand_dims({time_dim: peffective[time_dim]}).copy()
    # Filling/emptying bucket day after day
    for doy in range(1, peffective[time_dim].size):
        if kc_params is not None:
            if planting_date is not None:
                planted_since = planted_since + np.timedelta64(1, "D")
            else:
                planted_since = planted_since.where(
                    lambda x: np.isnat(x) == False,
                    other=xr.where(
                        sm.isel({time_dim: doy - 1}) >= sm_threshold, -1, np.nan
                    ).astype("timedelta64[D]"),
                ) + np.timedelta64(1, "D")
            kc = kc_inflex.interp(
                kc_periods=planted_since, kwargs={"fill_value": 1}
            ).where(lambda x: np.isnan(x) == False, other=1)
        et_crop[{time_dim: doy}] = kc * et.isel({time_dim: doy}, missing_dims='ignore')
        sm[{time_dim: doy}], drainage[{time_dim: doy}] = soil_plant_water_step(
            sm.isel({time_dim: doy - 1}),
            peffective.isel({time_dim: doy}),
            et_crop.isel({time_dim: doy}),
            taw,
        )
    if kc_params is not None and planting_date is None:
        planting_date = peffective[time_dim][doy].drop_vars(time_dim) - planted_since
    return sm, drainage, et_crop, planting_date