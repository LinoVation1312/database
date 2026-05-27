# Optimized Acoustic Absorption Curves Visualization

This project allows you to load, filter, and visualize acoustic absorption curves from Excel files containing acoustic data. It provides an interactive Streamlit interface that allows users to compare multiple samples based on various characteristics, and download the generated graphs in PDF or JPEG formats.

## Features

- Load data from an Excel file (with a "DATA" sheet).
- Filter samples based on various criteria: Trim Level, Supplier, Surface Mass, Thickness, Assembly Type.
- Interactive visualization of acoustic absorption curves with a logarithmic scale on the frequency axis.
- Ability to download the generated graphs in PDF or JPEG formats.


https://database-alpha.streamlit.app/


## Prerequisites if you want to host in Local

- Python 3.7 or higher
- Required Python libraries:
  - `streamlit`
  - `pandas`
  - `matplotlib`
  - `openpyxl`
  - `plotly`

You can install the required dependencies using the following command:

```pip install streamlit pandas matplotlib openpyxl numpy```

```git clone https://github.com/your-username/your-repository.git```
```cd your-repository```

```pip install -r requirements.txt```

```streamlit run test.py```

**Upload an Excel file containing the acoustic absorption data. The file should include a "DATA" sheet with the following (or similar) columns:**

sample_number_stn: Sample ID

trim_level: Trim level

material_family: Material family

material_supplier: Material supplier

surface_mass_gm2: Surface mass (g/m²)

thickness_mm: Thickness (mm)

assembly_type: Assembly type

frequency: Frequency (Hz)

alpha_cabin or alpha_kundt: Acoustic absorption coefficient



## Contributing

Fork this project.

Create a branch for your changes (git checkout -b feature/feature-name).

Make your changes and commit them (git commit -am 'Added feature X').

Push to the branch (git push origin feature/feature-name).

Open a pull request to propose your changes.


## Author

Lino Conord - Novice Developer - https://github.com/LinoVation1312

## License

This project is licensed under the MIT License - see the LICENSE file for details.

