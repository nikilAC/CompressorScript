import streamlit as st

import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go

import os, sys
import boto3
import io 
# Make page wide layout 
st. set_page_config(layout="wide")

# Confidential Keys for Accessing AWS Database
amzSecrets = st.secrets["AWSKeys"]

# s3 connection to call functions on AWS Stuff
s3_client = boto3.client('s3', aws_access_key_id = amzSecrets['aws_key_access'], aws_secret_access_key = amzSecrets['aws_secret'])
def get_bucket_list(bucketKey):
  s3 = boto3.resource('s3', aws_access_key_id = amzSecrets['aws_key_access'], aws_secret_access_key = amzSecrets['aws_secret'])
  my_bucket = s3.Bucket(amzSecrets[bucketKey])
  bucketnames = np.array([])
  # List out objects in s3 bucket
  for obj in my_bucket.objects.all():
    bucketnames = np.append(bucketnames, obj.key)

  return bucketnames

def get_drive_data(weatherDataFile):
    """Grab Data from AWS .
    """

    # Get Representative SN1 Data from AWS

    representativeData = s3_client.get_object(Bucket = amzSecrets["representativedatabucket"], Key = 'SN1_Representative_Data.csv')

    # Save representative data to df

    rep_df = pd.read_csv(io.StringIO(representativeData['Body'].read().decode('utf-8')))

    # Weather Data to base Estimations

    
    weatherData = s3_client.get_object(Bucket =  amzSecrets["weatherdatabucket"], Key = weatherDataFile)

    # Condition so code works with both csv and excel files
    if '.xlsx' in weatherDataFile:
      weather_df = pd.read_excel(io.BytesIO(weatherData['Body'].read()))
    else:
      weather_df = pd.read_csv(io.StringIO(weatherData['Body'].read().decode('utf-8')))

    # Change format of object such that it outputs as Df (Csv needs decoding to get the correct result)
    return rep_df, weather_df



# Adjusting the function to work efficiently on large datasets
def adjust_bladder_capacity_large(df, init_speed, min_capacity_pct, capacity_flip_pct, bladder_mass, initial_capacity=0):




    capacity = initial_capacity
    minute_capacity = initial_capacity
    decrement_added = init_speed
    df["OnOff"] = "Turned Off"
    first = True
    curOn = False
    minute_decrement_added = init_speed / 60
    low_decrement = np.round(convM3toKg(17.5)/60, 2)

    # Add date column so we can filter data to only include dates that are actually in our RH data.
    df["Date"] = pd.to_datetime(df['Timestamp']).dt.date


    df_minutes = pd.DataFrame()

    # very temporary fix
    if df.columns > 1:
      df_minutes['Timestamp'] = pd.date_range(start=df['Timestamp'].min(), end=df['Timestamp'].max(), freq='T')
    df_minutes["Date"] = pd.to_datetime(df_minutes['Timestamp']).dt.date
    df_minutes = df_minutes.query('Date in @df["Date"]')
    df_minutes = pd.merge(df_minutes, df, on='Timestamp', how='left')
    df_minutes['HourTime'] = pd.to_datetime(df_minutes['Timestamp'].dt.strftime('%Y-%m-%d %H:00:00'))
    df_minutes["rateDifference"] = 0



    # Forward fill the values to propagate the hourly values to each minute
    df_minutes.fillna(method='ffill', inplace=True)

    df_minutes["Interpolated_Value"] = df_minutes["Interpolated_Value"] / 60
    
    min_capacity_value = bladder_mass * (min_capacity_pct / 100)
    capacity_flip_value = bladder_mass * (capacity_flip_pct / 100)
    nonzeroDecrement = low_decrement
    minute_decrement_added = 0

    onOffList = []
    bladderCapacity = []
    rateDiff =[]
    interp_Vals = np.array(df_minutes["Interpolated_Value"])
    # Minute-Wise
    for i in range(len(df_minutes) - 1):
        
        # Band-aid for issue of "Turned Off" appearing twice in graphs
        if first:
          onOffList.append("Turned Off")
          #df_minutes['OnOff'].iloc[i] = "Compressor Off"
          bladderCapacity.append(minute_capacity)
          first = False
        

        # Getting Current Interpolated Values (inlet CO2 Into Bladder)
        interpolated_value = interp_Vals[i]

        # Calculating current Value to add
        added_value = interpolated_value - minute_decrement_added

        rateDiff.append(nonzeroDecrement - interpolated_value)

        if curOn:
          # Change to 17.5m^3 (or equiv in kg) if below capacity_flip_pct%, change to set speed if above
          if minute_capacity / bladder_mass < (capacity_flip_pct / 100):
            minute_decrement_added = low_decrement
            nonzeroDecrement = low_decrement


        if minute_capacity + added_value < min_capacity_value:
          # If our Minimum capacity is 0, ensure value doesn't go negative by setting it to 0
          minute_capacity = max(0, minute_capacity)
          minute_decrement_added = 0
          #df_minutes["OnOff"].iloc[i + 1] = "Turned Off"
          onOffList.append("Turned Off")
          # Turn off compressor to allow it to fill
          curOn = False
        elif (minute_capacity + added_value) >= capacity_flip_value:
          # If Maximum capacity is 100, ensure we don't go above bladder volume
          minute_capacity = min(bladder_mass, minute_capacity + added_value)
          # Set Decrement to highest possible value
          minute_decrement_added = init_speed / 60
          nonzeroDecrement = init_speed / 60
          #df_minutes["OnOff"].iloc[i + 1] = "Compressor On"
          if curOn: 
            onOffList.append("Compressor On")
          # Still update minute capacity
          minute_capacity = minute_capacity + added_value

          # If compressor is off, yet we are hitting bladder_mass+, turn it back on
          if not curOn:
            #df_minutes["OnOff"].iloc[i + 1] = "Turned On"
            onOffList.append("Turned On")
            curOn = True
        else:
          minute_capacity += added_value
          onOffList.append("Compressor On" if curOn else "Compressor Off")
        #df_minutes["Bladder Capacity"].iloc[i + 1] = minute_capacity
        bladderCapacity.append(minute_capacity)
    df_minutes["Bladder Capacity"] = pd.Series(bladderCapacity)
    df_minutes["OnOff"] = pd.Series(onOffList)
    df_minutes["rateDifference"] = pd.Series(rateDiff)
    #df_minutes["Bladder Capacity"].iloc[len(df_minutes) - 1] = 0  # Last row capacity set to 0 by default
    

    
    # # Hour-Wise
    # for i in range(len(df) - 1):

    #     # Creating current value to add (interpolated - init_speed (flow into - flow out of bladder))
    #     # Current function assumes that the flow into flow out of bladder happen at same time, is there an opportunity for waste due to the bladder being full
    #     interpolated_value = df['Interpolated_Value'].iloc[i]
    #     added_value = interpolated_value - decrement_added

    #     # Stop Compressor if capacity goes under minimum capacity until it is back at the value corresponding to capacity_flip_pct
    #     if capacity + added_value <= bladder_mass * (min_capacity_pct / 100):
    #         # If our minimum capacity is 0, ensure value doesn't go negative by setting it to 0
    #         if min_capacity_pct == 0:
    #           capacity = bladder_mass
    #         decrement_added = 0
    #         df["OnOff"].iloc[i + 1] = str(-1)
    #         curOn = False
    #     elif (capacity + added_value) >= bladder_mass * (capacity_flip_pct / 100):
    #         # If Maximum capacity is 100, ensure we don't go above bladder volume
    #         if capacity_flip_pct == 100:
    #           capacity = bladder_mass
    #         decrement_added = init_speed
    #         if curOn == False:
    #           df["OnOff"].iloc[i + 1] = str(1)
    #           curOn = True

    #     else:
    #       capacity = capacity + added_value

    #     # Set next bladder capacity value based on current bladder capacity and interpolated value
    #     df['Bladder Capacity'].iloc[i + 1] = capacity
    
    # df["Bladder Capacity"].iloc[len(df) - 1] = 0  # Last row capacity set to 0 by default
    
    return df_minutes, df
def convM3toKg(volume):
  # Convert to Liters
  volume *= 1000

  # Convert to Grams
  mass = volume * 1.8307 

  # Convert to Kg
  return mass / 1000

def volFlowEstimation(df, weatherData, DAC_ct=1, daterange=['2023-06-01', '2024-02-01'], init_speed=17.5, min_capacity_pct = 10, capacity_flip_pct=90, bladder_mass=23.2,  initial_capacity=0):


 
  init_speed = np.round(convM3toKg(init_speed), 2)
  bladder_mass = np.round(convM3toKg(bladder_mass), 2)
  low_speed = np.round(convM3toKg(17.5), 2)
  
  
  #Dictionaries    
  
   # Changing initial capacity in case it is lower than the minimun capacity we want to have
  if initial_capacity < (min_capacity_pct / 100) * bladder_mass:
    initial_capacity = (min_capacity_pct/ 100) * bladder_mass
  pd.options.mode.chained_assignment = None
  #Dictionaries

  hours_in_month = {
    "01": 31 * 24,
    "02": 28 * 24,  # 29 * 24 for leap year
    "03": 31 * 24,
    "04": 30 * 24,
    "05": 31 * 24,
    "06": 30 * 24,
    "07": 31 * 24,
    "08": 31 * 24,
    "09": 30 * 24,
    "10": 31 * 24,
    "11": 30 * 24,
    "12": 31 * 24
      }


  monthNumToName = {
        "1" : "January",
        "2" : "February",
        "3" : "March",
        "4" : "April",
        "5" : "May",
        "6" : "June",
        "7" : "July",
        "8" : "August",
        "9" : "September",
        "10" : "October",
        "11" : "November",
        "12" : "December"
    }

  colors = {
      2: "blue",
      17: "red"
  }

  # Getting and collecting weather data


  # Drop all non-numeric rows from weather date (in case of null values)) 
  # Assuming temperature is Temperature_degC And humidity is RH_percent {O(n) Runtime}
  weatherData = weatherData[pd.to_numeric(weatherData['Temperature_degC'], errors='coerce').notnull()]
  weatherData = weatherData[pd.to_numeric(weatherData['RH_percent'], errors='coerce').notnull()]


    #Test Later
    #weatherData[['Temperature_degC', "RH_percent"]] = weatherData[['Temperature_degC', "RH_percent"]].apply(pd.to_numeric)

  # Make Temperature and RH Percent columns Numeric {O(n) Runtime}
  weatherData['Temperature_degC'] = pd.to_numeric(weatherData['Temperature_degC'])
  weatherData['RH_percent'] = pd.to_numeric(weatherData['RH_percent'])

  weatherData.index = np.arange(0, len(weatherData))



  
  # MEAN MONTHLY ADDITION FROM RIDGE CLIMATE DATA
  # RHData.loc[-1] = 	["3/1/2023 12:00",	40.1,	83]
  # RHData.loc[-2] = 	["3/1/2023 12:00",	24,	58]
  # RHData.loc[-3] = 	["3/1/2023 12:00",	9.4,	31]
  # RHData.loc[-4] = 	["4/1/2023 12:00",	44.4,	78]
  # RHData.loc[-5] = 	["4/1/2023 12:00",	28.8,	49]
  # RHData.loc[-6] = 	["4/1/2023 12:00",	12.5,	22]
  # RHData.loc[-7] = 	["5/1/2023 12:00",	48.1,	79]
  # RHData.loc[-8] = 	["5/1/2023 12:00",	33.2,	46]
  # RHData.loc[-9] = 	["5/1/2023 12:00",	15.7,	18]
  
  # Calculating CO2 Purity (/1000 to kg)
  df["CO2_Purity-Corrected_Kg"] = df[" CO2_Fox_g"] * (df[" DAC_CO2_Percent"] / 100) / 1000

  # Calculating kg Per Hour Again, Directly from CO2_Purity_corrected (CycleSecs to cycle time, * 3600 to hour)
  df["CO2_Kg_Per_Hour"] = df["CO2_Purity-Corrected_Kg"] / df[" CycleSecs"] * 3600

  # Calculating Kg Per Day
  df["CO2_Kg_Per_Day"] = df["CO2_Kg_Per_Hour"] * 24

  # Making various figures
  fig = go.Figure()
  dayBar = go.Figure()
  diffFig = go.Figure()
  freqBar = go.Figure()

  for contactor in df["Contactor Type"].unique():
    freqBar = go.Figure()
    contactDf = df

    # Do not consider first three towers for type 17 brick
    if contactor == 17:
      contactDf = contactDf.query('`Contactor Type` == 17 and ` DAC_TowerNum` > 3')
    else:
      contactDf = contactDf.query('`Contactor Type` == @contactor')

    # Creating coefficients based on RH
    poly_fit = np.polyfit(contactDf[" AirRelHumid_In"], contactDf["CO2_Kg_Per_Hour"], deg=3)

    # Find maximum and minimum RH Values in Operational data, so we can set any values not between these to one or the other
    maxRH = contactDf[' AirRelHumid_In'].max()
    minRH = contactDf[' AirRelHumid_In'].min()

    print(f"Contactor Type {contactor}")

    # Accounting for values outside our range

    weatherData.loc[weatherData['RH_percent'] < minRH, 'RH_percent'] = minRH + .01
    weatherData.loc[weatherData['RH_percent'] > maxRH, 'RH_percent'] = maxRH


    # Include timestamps for added granularity (generalize for different times of data)
    weatherData["Timestamp"] = pd.to_datetime(weatherData["Timestamp"])
    weatherData["Month"] = weatherData["Timestamp"].dt.month
    weatherData["Day"] = weatherData["Timestamp"].dt.day
    weatherData["Date"] = weatherData["Timestamp"].dt.date
    weatherData = weatherData.query('@daterange[1] > Timestamp > @daterange[0]').sort_values(by="Timestamp")



    # Calculating Values for Interpolation: (y2 - y1) / (x2 - x1) * (x - x1) + y1 = y

    model = np.poly1d(poly_fit)
    weatherData["Interpolated_Value"] = model(weatherData["RH_percent"]) * DAC_ct
      
    # Create a complete date range, including the missing dates
    full_dates = pd.date_range(start=min(weatherData["Timestamp"]), end=max(weatherData["Timestamp"]), freq='H')

    plottedCopy = weatherData.copy()

    plottedCopy.index = plottedCopy["Timestamp"]

    # Reindex the DataFrame to include the full date range, filling missing entries with None
    plottedDataWeather = plottedCopy.reindex(full_dates, fill_value=None)

    # Plotting average RH Values of NCCC Data
    #pivotScatter.add_trace(go.Scatter(x=contactPivotTbl["Average RH"], y=contactPivotTbl["Avg Production Volume (kg/h)"], mode='markers+lines', name = f"Contactor Type: {contactor}"))

    # Plotting hour-by-hour line/scatter plot
    st.write("Would love to add a toggle marker option")
    
    fig.add_trace(go.Scatter(x=plottedDataWeather["Timestamp"], y=plottedDataWeather["Interpolated_Value"], mode='lines', name = f"Contactor Type: {contactor}", marker_color = colors[contactor], yaxis= 'y1', connectgaps=False))
    

    # Create day-based table for 8 DACs
    dayMerge = weatherData.groupby("Date").agg({"Interpolated_Value": "sum"}).reset_index()
    dayMerge.rename(columns = {"Interpolated_Value": "Full 44.01 Interpolated Value"}, inplace = True)


    # Table with Bladder Capacity
    weatherData["Bladder Capacity"] = initial_capacity
    
    minuteBladderTbl, bladderTbl = adjust_bladder_capacity_large(weatherData, init_speed, min_capacity_pct, capacity_flip_pct, bladder_mass, initial_capacity)





    #iffFig.add_trace(go.Scatter()


    minuteBladderTbl['Formatted Timestamp'] = minuteBladderTbl['Timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
    bladderTbl['Formatted Timestamp'] = bladderTbl['Timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')

    # Minute Based Bladder Capacity

    diffFig = px.scatter(minuteBladderTbl[["Formatted Timestamp", "HourTime", "rateDifference"]].groupby("HourTime").agg("sum").reset_index(), x = "HourTime", y = "rateDifference", title = "Difference in Outlet and Inlet Bladder CO2 Mass (kg)",  template = "plotly_white" ,
                 
                labels={
                     "Bladder Capacity": "Mass Difference (kg)",
                     
                     "HourTime": "Time"
                 })

    diffFig.update_layout(
      yaxis_title="Mass difference (kg)",
      title={
          'text': f'<b>Bladder Inlet/Outlet Mass Difference Per Hour (kg)</b>',
          'y':.95,
          'x':0.5,
          'xanchor': 'center',
          'yanchor': 'top',
          'font': {
              'size': 24,
              'family': 'Arial, sans-serif',

          }
      },
      legend=dict(font=dict(size= 15)),

      xaxis = dict(tickfont=dict(
            size=15,  # Increase the font size here
            color='black'
        ),
      titlefont=dict(
            size=20,  # Increase the font size here
            color='black'
        )),
       yaxis = dict(
         range = [0, None],
         tickfont=dict(
            size=15,  # Increase the font size here
            color='black'
        ),
      titlefont=dict(
            size=20,  # Increase the font size here
            color='black'
        )),

      annotations=[
        dict(
            text=f'Type {contactor} | {DAC_ct} DAC | {init_speed}:{low_speed} kg/h Compressor Flow Rate',
            x=0.475,
            y=1.1,
            xref='paper',
            xanchor = 'center',
            yanchor = 'top',
            yref='paper',
            showarrow=False,
            font=dict(
                size=16,
                color='black',
                family = 'Arial, sans-serif'
            )
        ),

    
    ],
      images=[dict(
            source='https://assets-global.website-files.com/63c8119087b31650e9ba22d1/63c8119087b3160b9bba2367_logo_black.svg',  # Replace with your image URL or local path
            xref='paper', yref='paper',
            x=.95, y=1.1,
            sizex=0.1, sizey=0.1,
            xanchor='center', yanchor='bottom'
        )]
    )
    
    # Plot minute-based bladder capacity
    minuteBladderCapacityFig = px.scatter(minuteBladderTbl, x = "Formatted Timestamp", y = "Bladder Capacity", title = "Bladder Mass Simulation (kg)", color = "OnOff", template = "plotly_white",  color_discrete_sequence=['darkred', 'red', 'green','lightgreen'] ,
                 opacity=0.7,
                labels={
                     "Bladder Capacity": "Bladder Mass (kg)",
                     "OnOff": "Status",
                     "Formatted Timestamp": "Time"
                 })

    minuteBladderCapacityFig.update_layout(
      title={
          'text': f'<b>Bladder Mass Simulation (kg)</b>',
          'y':.95,
          'x':0.479,
          'xanchor': 'center',
          'yanchor': 'top',
          'font': {
              'size': 24,
              'family': 'Arial, sans-serif',

          }
      },
      legend=dict(font=dict(size= 15)),

      xaxis = dict(tickfont=dict(
            size=15,  # Increase the font size here
            color='black'
        ),
      titlefont=dict(
            size=20,  # Increase the font size here
            color='black'
        )),
       yaxis = dict(
         range=[0, None],
         tickfont=dict(
            size=15,  # Increase the font size here
            color='black'
        ),
      titlefont=dict(
            size=20,  # Increase the font size here
            color='black'
        )),

      annotations=[
        dict(
            text=f'Type {contactor} | {DAC_ct} DAC | {init_speed}:{low_speed} kg/h Compressor Flow Rate | Bladder Mass: {bladder_mass} kg',
            x=0.5,
            y=1.1,
            xref='paper',
            xanchor = 'center',
            yanchor = 'top',
            yref='paper',
            showarrow=False,
            font=dict(
                size=16,
                color='black',
                family = 'Arial, sans-serif'
            )
        ),

        # Line for flipping capacity Percentage
        dict(
          x=minuteBladderTbl['Formatted Timestamp'].max(),  # Place it at the end of the x-axis
          xanchor = 'left',
          yanchor = 'middle',
          y=(capacity_flip_pct / 100) * bladder_mass,
          text=f"{capacity_flip_pct}% Bladder Capacity",
          showarrow=False,
          yshift=9,  # Shift text upwards
          xshift = 5  # Shift text to the right
        ),

        # Line for Maximum Capacity
        dict(
          x=minuteBladderTbl['Formatted Timestamp'].max(),  # Place it at the end of the x-axis
          xanchor = 'left',
          yanchor = 'middle',
          y=bladder_mass,
          text=f"Maximum Bladder Capacity",
          showarrow=False,
          yshift=9,  # Shift text upwards
          xshift = 5  # Shift text to the right
        ),

        # Label for compressor turning 0 capacity perecnt
        dict(
          x=minuteBladderTbl['Formatted Timestamp'].max(),  # Place it at the end of the x-axis
          xanchor = 'left',
          yanchor = 'middle',
          y=(min_capacity_pct / 100) * bladder_mass,
          text=f"{min_capacity_pct}% Bladder Capacity",
          showarrow=False,
          yshift=9,  # Shift text upwards
          xshift = 5  # Shift text to the right
        )
    ],
      images=[dict(
            source='https://assets-global.website-files.com/63c8119087b31650e9ba22d1/63c8119087b3160b9bba2367_logo_black.svg',  # Replace with your image URL or local path
            xref='paper', yref='paper',
            x=.95, y=1.1,
            sizex=0.1, sizey=0.1,
            xanchor='center', yanchor='bottom'
        )]
    )

    # Add a capacity flip line to the plot
    minuteBladderCapacityFig.add_shape(
          type='line',
          x0=minuteBladderTbl['Formatted Timestamp'].min(),
          y0=(capacity_flip_pct / 100) * bladder_mass,
          x1=minuteBladderTbl['Formatted Timestamp'].max(),
          y1=(capacity_flip_pct / 100) * bladder_mass,
          line=dict(
              color="RoyalBlue",
              width=2,
              dash="dot",
          ),
      )


    # Add a maximum bladder capacity line to plot
    minuteBladderCapacityFig.add_shape(
          type='line',
          x0=minuteBladderTbl['Formatted Timestamp'].min(),
          y0=bladder_mass,
          x1=minuteBladderTbl['Formatted Timestamp'].max(),
          y1=bladder_mass,
          line=dict(
              color="purple",
              width=2,
              dash="dot",
          ),
      )

    minuteBladderCapacityFig.add_shape(
          type='line',
          x0=minuteBladderTbl['Formatted Timestamp'].min(),
          y0=(min_capacity_pct / 100) * bladder_mass,
          x1=minuteBladderTbl['Formatted Timestamp'].max(),
          y1=(min_capacity_pct / 100) * bladder_mass,
          line=dict(
              color="purple",
              width=2,
              dash="dot",
          ),
      )



    # Add an image to the plot

    minuteBladderCapacityFig.for_each_trace(
        lambda trace: trace.update(marker=dict(size=8)) if "Turned" in trace.name else trace.update(marker=dict(size=5))
    )


    #minuteBladderCapacityFig.show()
    st.plotly_chart(minuteBladderCapacityFig, use_container_width=True)
    st.plotly_chart(diffFig, use_container_width=True)

    # Adding Production line to fig
    #fig.add_trace(go.Scatter(x = minuteBladderTbl["Formatted Timestamp"], y = minuteBladderTbl["Interpolated_Value"], mode = 'markers'))


    #Hour Based Bladder Capacity
    
    # px.scatter(bladderTbl, x = "Formatted Timestamp", y = "Bladder Capacity",color = "OnOff", labels={
    #                   "Bladder Capacity": r"Bladder Capacity (m³)",
    #                   "OnOff": "Status",
    #                   "Formatted Timestamp": "Time"
    #               }).show()
      

    # Making day based bar chart of frequency of on off
    minuteBladderTbl["Date"] = minuteBladderTbl["Timestamp"].dt.date.astype(str)
    dailyFreqTable = pd.pivot_table(minuteBladderTbl, columns = ["OnOff"], index = ["Date"], aggfunc = "size").reset_index().fillna(0)

    freqBar.add_trace(go.Bar(x=dailyFreqTable["Date"], y=dailyFreqTable["Turned Off"].astype(int), marker_color = 'darkred', name = "Turned Off"))

    #freqBar.add_trace(go.Bar(x=dailyFreqTable["Date"], y=dailyFreqTable["Turned On"].astype(int), marker_color = 'green', name = "Turned On"))

    freqBar.update_layout(

    xaxis_title = "Date",
    legend=dict(font=dict(size= 15)),
    xaxis = dict(
                 tickfont=dict(
            size=15,  # Increase the font size here
            color='black'
        ),

    titlefont=dict(
            size=20,  # Increase the font size here
            color='black'
        )),


      template = "plotly_white",
      barmode='group',
      bargap=.05,
      bargroupgap=.3,
      
      yaxis_title = "Turn-Off Operations",
      yaxis=dict(
        tickmode='linear',
        tick0=0,
        dtick=1,
        tickfont=dict(
            size=15,  # Increase the font size here
            color='black'
    ),
            titlefont=dict(
            size=20,  # Increase the font size here
            color='black'
        )),
      title={
          'text': f'<b>Compressor Turn-Off Operations Per Day</b>',
          'y':.95,
          'x':0.5,
          'xanchor': 'center',
          'yanchor': 'top',
          'font': {
              'size': 24,
              'family': 'Arial, sans-serif',

          }
      },
      annotations=[
        dict(
            text=f'Type {contactor} | {DAC_ct} DAC | {init_speed}:{low_speed} kg/h Compressor Flow Rate | Bladder Volume: {bladder_mass} kg | Average Daily Turn-Off Operations: <b>{np.round(np.mean(dailyFreqTable["Turned Off"]), 2)}<b>',
            x=0.5,
            y=1.1,
            xref='paper',
            xanchor = 'center',
            yanchor = 'top',
            yref='paper',
            showarrow=False,
            font=dict(
                size=16,
                color='black',
                family = 'Arial, sans-serif'
            )
        ),
      

    ],
      images=[dict(
            source='https://assets-global.website-files.com/63c8119087b31650e9ba22d1/63c8119087b3160b9bba2367_logo_black.svg',  # Replace with your image URL or local path
            xref='paper', yref='paper',
            x=.95, y=1.1,
            sizex=0.1, sizey=0.1,
            xanchor='center', yanchor='bottom'
        )]
    )
    #freqBar.show()
    st.plotly_chart(freqBar, use_container_width=True)
    # Plotting Day-Based Bar Chart
    dayBar.add_trace(go.Bar(x=dayMerge["Date"], y=dayMerge["Full 44.01 Interpolated Value"], marker_color = colors[contactor],  name = f"Contactor Type: {contactor}"))


  fig.update_layout(
    xaxis_title="Date",
    yaxis_title="CO2 Production Volume (kg/h)",
    yaxis = dict(
      range=[0, None],
                 tickfont=dict(
            size=15,  # Increase the font size here
            color='black'
        ),
         titlefont=dict(
            size=20,  # Increase the font size here
            color='black'
        ),
    ),
    xaxis = dict(
      
                 tickfont=dict(
            size=15,  # Increase the font size here
            color='black'
        ),
         titlefont=dict(
            size=20,  # Increase the font size here
            color='black'
        ),
    ),

      title={
          'text': f'<b>CO2 Production</b>',
          'y':.95,
          'x':0.5,
          'xanchor': 'center',
          'yanchor': 'top',
          'font': {
              'size': 24,
              'family': 'Arial, sans-serif',

          }
      },
    
      images=[dict(
            source='https://assets-global.website-files.com/63c8119087b31650e9ba22d1/63c8119087b3160b9bba2367_logo_black.svg',  # Replace with your image URL or local path
            xref='paper', yref='paper',
            x=.95, y=1.1,
            sizex=0.1, sizey=0.1,
            xanchor='center', yanchor='bottom'
        )]

  )
  st.plotly_chart(fig, use_container_width=True)
  #fig.show()

  dayBar.update_layout(xaxis_title = "Date", yaxis_title = "CO2 Production Volume (kg)", barmode = "group",   bargap = .2, legend = dict(groupclick = "toggleitem"))
  st.plotly_chart(dayBar, use_container_width=True)
  #dayBar.show()

  #return fig



#df, rhPath, DAC_ct=1, daterange=['2023-06-01', '2024-02-01'], init_speed=17.5, min_capacity_pct = 10, capacity_flip_pct=90, bladder_mass=23.2,  initial_capacity=0


import time

weatherDataFile = st.sidebar.selectbox("Choose Weather File", get_bucket_list("weatherdatabucket"))
df, weatherData = get_drive_data(weatherDataFile)

# Extract unique dates
unique_dates = sorted(weatherData['Timestamp'])
unique_dates = np.unique(pd.to_datetime(unique_dates).date)
unique_dates = pd.Series(sorted(unique_dates)) 
st.title('Compressor Start/Stop and Bladder Volume Simulation')

# Create date pickers

# Create two columns for start and end date selection
col1, col2 = st.columns(2)

# Start date selector in the first column
with col1:
    start_date = st.date_input(
    'Start date',
    min_value=min(unique_dates),
    max_value=max(unique_dates),
    value=min(unique_dates),
    help="Select the start date."
    )

# Filter valid end dates based on the selected start date
valid_end_dates = unique_dates[unique_dates >= start_date]

if 'end date' not in st.session_state:
  st.session_state['end date'] = min(valid_end_dates)
# End date selector in the second column
with col2:
  end_date = st.date_input(
  'End date',
  min_value=min(valid_end_dates),
  max_value=max(valid_end_dates),
  value=max(min(valid_end_dates), st.session_state['end date']),
  help="Select the end date."
  )



if start_date > end_date:
    st.error('End date must be after start date.')
    end_date = start_date
    
else:
    date_range = (end_date - start_date).days
st.session_state['end date'] = end_date

dacCT = st.sidebar.number_input("Number of DAC Units", value = 8)
minPctShutoff = st.sidebar.number_input("Minimum % Capacity Before Compressor Shutoff", value=10)
maxPctTurndown = st.sidebar.number_input("Maximum % Capacity Before 50% Compressor Turndown", value=90)
bladderVol = st.sidebar.number_input("Specify Bladder Volume (in kg)", value=23.2)
# DOING IT WITHOUT STREAMLIT rhPath = input("Enter RH and Temperature File (.csv or .xlsx)")


if st.button("Generate"):
  with st.spinner('Calculating...'):
    time.sleep(5)
    volFlowEstimation(df, weatherData, dacCT, [start_date, end_date], 35, minPctShutoff, maxPctTurndown, bladderVol, 0)

