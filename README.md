An add-on to fix and fill hole on your meshes


<img width="1200" height="600" alt="fill messh" src="https://github.com/user-attachments/assets/6016797e-5d46-4948-a64d-a3fbcafb1013" />

#

This tool streamlines the process of repairing complex and irregular 3D models by automatically detecting and filling holes. It identifies gaps in geometry and allows users to close specific areas or seal an entire mesh instantly.


Includes features for snapping selected vertices to a target group and merging nearby vertices within a specified distance to maintain clean topology. It utilizes techniques like Grid Fill, Hole Fill, and Contextual Create, alongside a Simple Bridge Fill option for manual edge selection. It is particularly useful for preparing scanned data, remeshed models, and sculpted objects for watertight geometry.

# Installation
Download the ZIP file.

Open Blender and go to **Edit** > **Preferences** > **Add-ons**.

Click **Install**, select the ZIP file, and click **Install Add-on**.

Enable the add-on by checking the corresponding box.

Access **Fill Mesh** in the **N menu** (sidebar) under the **Fill Mesh tab**.

# How to Use Fill Mesh
1. **Select an object** : Choose a mesh that contains holes to fill.

2. **Choose the filling option**:<img width="1545" height="969" alt="fill_mesh_tuto9" src="https://github.com/user-attachments/assets/39cab105-4b90-442b-9a5f-6b98da11d5a3" />


	• **Repair Notches** : Fills missing border quads to turn an irregular hole into a clean, grid-fillable boundary. <img width="1545" height="969" alt="fill_mesh_tuto5" src="https://github.com/user-attachments/assets/55dedcd3-abaf-41f4-9988-968e40ddfe81" />

	• **Grid Fill** : Fills the selected hole boundary with a clean quad grid using Blender's native Grid Fill operator.

	• **Detect Hole** : Automatic Hole Detection no need to manually select edges, finds the gaps for you.
1<img width="1545" height="969" alt="fill_mesh_tuto6" src="https://github.com/user-attachments/assets/00da7df3-1c49-4802-82b8-256d64a016f7" />


	• **Snap Vertex** : Automatically moves selected vertices closer to a target group of vertices based on snapping settings.

	• **Merge Vertex Group** : Fuses nearby vertices group within a set distance, ensuring a cleaner topology by connecting the closest points.<img width="1591" height="985" alt="Animation_Fill_Mesh_14" src="https://github.com/user-attachments/assets/20b6593c-eb4e-461f-bfe8-530170f0e52e" />


	• **Fill Mesh** : Fills holes by adding a mesh, providing better continuity and a cleaner structure on flat surfaces.

	• **Fill Shape** : Analyzes the area and reconstructs the missing shape by adding a more complex mesh, perfect for curved and organic surfaces.<img width="1545" height="969" alt="fill_mesh_tuto7" src="https://github.com/user-attachments/assets/fba6f5f6-8c8f-45a8-8486-9de66ae3a37d" />


	• **Simple Quad Fill** : Automatically extends and reconstructs open mesh boundaries by generating clean quad-based topology from selected boundary edges.<img width="1545" height="969" alt="fill_mesh_tuto10" src="https://github.com/user-attachments/assets/4e75d469-4216-4a23-ac2b-f9cba822cd97" />


	• **Click on the option of your choice** : The add-on analyzes the mesh and applies the selected option.

	• **Adjust the settings if needed** : Modify the fill density or apply smoothing for better results.

	• **Validate the changes** : Once satisfied, apply the corrections to permanently integrate the new geometry.

# Advanced Options
• **Curvature Adaptation** : Analyzes the topology to optimally distribute new vertices.

• **Smart Vertex Distribution** : Prevents the creation of overly large or misaligned faces.

• **Progressive Smoothing** : Integrates a relaxation option to soften mesh edges after repair.
  
