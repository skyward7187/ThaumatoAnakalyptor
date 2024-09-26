### Julian Schilliger - ThaumatoAnakalyptor - Vesuvius Challenge 2024

import open3d as o3d
import igl
import numpy as np
from scipy.spatial import KDTree
from scipy.cluster.hierarchy import fcluster, linkage
import argparse
import os
from .split_mesh import MeshSplitter
from .sheet_to_mesh import scale_points, shuffling_points_axis
import tempfile
from tqdm import tqdm
import pickle
from copy import deepcopy

def setup_closest_triangles(mesh):
    mesh = o3d.t.geometry.TriangleMesh.from_legacy(mesh)
    # Create a scene and add the triangle mesh
    scene = o3d.t.geometry.RaycastingScene()
    _ = scene.add_triangles(mesh)  # we do not need the geometry ID for mesh
    return scene

def load_mesh_vertices(obj_file):
    # copy mesh to tempfile
    with tempfile.NamedTemporaryFile(suffix=".obj") as temp_file:
        # copy mesh to tempfile
        temp_path = temp_file.name
        # os copy
        os.system(f"cp {obj_file} {temp_path}")
        # load mesh
        mesh = o3d.io.read_triangle_mesh(temp_path, print_progress=True)

    # Coordinate transform to pointcloud coordinate system
    vertices = np.asarray(mesh.vertices)
    vertices += 500
    vertices = vertices[:,[1, 2, 0]]
    mesh.vertices = o3d.utility.Vector3dVector(vertices)
    normals = np.asarray(mesh.vertex_normals)
    normals = normals[:,[1, 2, 0]]
    mesh.vertex_normals = o3d.utility.Vector3dVector(normals)

    triangles = np.asarray(mesh.triangles)

    print(f"Number triangles in mesh: {len(triangles)}")

    scene = setup_closest_triangles(mesh)
    return mesh, vertices, triangles, scene

def calculate_winding_angle(default_splitter):
    # splitter.compute_uv_with_bfs(np.random.randint(0, splitter.vertices_np.shape[0]))
    winding_angles = default_splitter.vertices_np[:, 0]
    return winding_angles

def calculate_winding_angle_pointcloud_instance(points, default_splitter):
    # splitter.compute_uv_with_bfs(np.random.randint(0, splitter.vertices_np.shape[0]))
    winding_angles = np.zeros(len(points))
    for i in range(len(points)):
        winding_angles[i] = default_splitter.angle_between_vertices(np.array([0.0, 0.0, 0.0]), points[i])

    # normalize to range +-180 around start point
    start_winding_angle = winding_angles[0]
    mask_gt = winding_angles > start_winding_angle + 180
    mask_lt = winding_angles < start_winding_angle - 180
    winding_angles[mask_gt] -= 360
    winding_angles[mask_lt] += 360
    assert np.all(winding_angles >= start_winding_angle - 180), f"Minimum winding angle is below -180 away from start winding angle: {np.min(winding_angles)}"
    assert np.all(winding_angles <= start_winding_angle + 180), f"Maximum winding angle is above 180 away from start winding angle: {np.max(winding_angles)}"
    return winding_angles

def calculate_winding_angle_pointcloud_raw(points, gt_splitter):
    # splitter.compute_uv_with_bfs(np.random.randint(0, splitter.vertices_np.shape[0]))
    winding_angles = np.zeros(len(points))
    for i in range(len(points)):
        winding_angles[i] = gt_splitter.angle_between_vertices(np.array([0.0, 0.0, 0.0]), points[i])

    # normalize to range +-180 around start point
    start_winding_angle = winding_angles[0]
    mask_gt = winding_angles > start_winding_angle + 180
    mask_lt = winding_angles < start_winding_angle - 180
    winding_angles[mask_gt] -= 360
    winding_angles[mask_lt] += 360
    assert np.all(winding_angles >= start_winding_angle - 180), f"Minimum winding angle is below -180 away from start winding angle: {np.min(winding_angles)}"
    assert np.all(winding_angles <= start_winding_angle + 180), f"Maximum winding angle is above 180 away from start winding angle: {np.max(winding_angles)}"
    return winding_angles

def find_closest_triangles(points, scene):
    # Find the closest triangle and the distance for each point
    query_points = o3d.core.Tensor(points, dtype=o3d.core.Dtype.Float32)

    # We compute the closest point on the surface for the point at position [0,0,0].
    ans = scene.compute_closest_points(query_points)
    points_on_triangle = ans['points'].numpy()
    # distances = np.sqrt((points_on_triangle - points ** 2).sum(-1))
    distances = np.linalg.norm(points_on_triangle - points, axis=-1)
    triangles_id = ans['primitive_ids'].numpy()
    return triangles_id, distances

def find_closest_triangles_same_winding(vertices1, winding_angles1, winding_angles2, triangles2, scene2, default_splitter):
    # optimizeable normal = umbilicus point - vertex
    umbilicus_xy1_x, umbilicus_xy1_y, umbilicus_xy1_z = default_splitter.interpolate_umbilicus(vertices1[:, 1] - 500)
    umbilicus_xy1 = np.stack([umbilicus_xy1_y, umbilicus_xy1_z, umbilicus_xy1_x], axis=-1) + 500
    optimizable_normals1 = vertices1 - umbilicus_xy1
    # normalize the normals
    optimizable_normals1 = optimizable_normals1 / np.linalg.norm(optimizable_normals1, axis=-1)[:, None]

    # list_intersections from open3d raycasting
    print(f"Total number of vertices: {len(vertices1)}")
    nr_sample = 1000
    directions1_0 = {}
    distances1_0 = {}
    directions1_1 = {}
    distances1_1 = {}
    for i in tqdm(range(0, len(vertices1), nr_sample), desc="list_intersections (vertex + normal all triangle intersections)"):
        # print(f"Processing vertices {i} to {i+nr_sample}")
        end_v = min(i+nr_sample, len(vertices1))
        vertices1_sample = vertices1[i:end_v]
        normals1_sample = optimizable_normals1[i:end_v]
        # Concatenate vertices and normals
        rays1_direction1 = np.concatenate([vertices1_sample, normals1_sample], axis=-1).astype(np.float32)
        rays1_direction2 = np.concatenate([vertices1_sample, -normals1_sample], axis=-1).astype(np.float32)

        # Compute the ray intersections.
        lx1_direction1 = scene2.list_intersections(rays1_direction1)
        lx1_direction1 = {k:v.numpy() for k,v in lx1_direction1.items()}

        lx1_direction2 = scene2.list_intersections(rays1_direction2)
        lx1_direction2 = {k:v.numpy() for k,v in lx1_direction2.items()}

        # Check if all the intersections are the same. first start by the number of hits
        hits1 = lx1_direction1["ray_ids"].shape[0]
        hits2 = lx1_direction2["ray_ids"].shape[0]
        # assert hits1 == hits2, f"Number of hits is different: {hits1} vs {hits2}"
        # print(f"Number of hits: {hits1} vs {hits2}")

        ray_splits = lx1_direction1["ray_splits"]
        ray_ids = lx1_direction1["ray_ids"]
        primitive_ids = lx1_direction1["primitive_ids"]
        t_hit = lx1_direction1["t_hit"]
        for ray_id, (start, end) in enumerate(zip(ray_splits[:-1], ray_splits[1:])):
            w1 = winding_angles1[i+ray_id]
            best_distance = float("inf")
            best_normal = None
            for j, triangle_id in enumerate(primitive_ids[start:end]):
                ray_id_check = ray_ids[start+j]
                assert ray_id == ray_id_check, f"Ray id {ray_id} is different from ray id check {ray_id_check}"
                t2 = triangles2[triangle_id]
                w2 = winding_angles2[t2[0]]
                assert t_hit[start+j] >= 0, f"t_hit is negative: {t_hit[start+j]}"
                if abs(w1 - w2) < 45 and abs(t_hit[start+j]) < best_distance:
                    best_distance = abs(t_hit[start+j])
                    best_normal = normals1_sample[ray_id]
            # Add the best normal and distance
            directions1_0[i+ray_id] = 1
            distances1_0[i+ray_id] = best_distance                
            
        ray_splits = lx1_direction2["ray_splits"]
        ray_ids = lx1_direction2["ray_ids"]
        primitive_ids = lx1_direction2["primitive_ids"]
        t_hit = lx1_direction2["t_hit"]
        for ray_id, (start, end) in enumerate(zip(ray_splits[:-1], ray_splits[1:])):
            w1 = winding_angles1[i+ray_id]
            best_distance = float("inf")
            best_normal = None
            for j, triangle_id in enumerate(primitive_ids[start:end]):
                ray_id_check = ray_ids[start+j]
                assert ray_id == ray_id_check, f"Ray id {ray_id} is different from ray id check {ray_id_check}"
                t2 = triangles2[triangle_id]
                w2 = winding_angles2[t2[0]]
                assert t_hit[start+j] >= 0, f"t_hit is negative: {t_hit[start+j]}"
                if abs(w1 - w2) < 45 and abs(t_hit[start+j]) < best_distance:
                    best_distance = abs(t_hit[start+j])
                    best_normal = -normals1_sample[ray_id]
            # Add the best normal and distance
            directions1_1[i+ray_id] = -1
            distances1_1[i+ray_id] = best_distance

    directions = np.zeros(len(vertices1), dtype=np.float32)
    distances = np.zeros(len(vertices1), dtype=np.float32)
    for ray_id in directions1_0:
        if directions1_0[ray_id] is None or (directions1_1[ray_id] is not None and distances1_1[ray_id] < distances1_0[ray_id]):
            directions[ray_id] = directions1_1[ray_id]
            distances[ray_id] = distances1_1[ray_id]
        elif directions1_0[ray_id] is not None:
            assert ray_id in directions1_0, f"Ray id {ray_id} not in directions1_0"
            directions[ray_id] = directions1_0[ray_id]
            distances[ray_id] = distances1_0[ray_id]
        else:
            distances[ray_id] = float("inf")
    
    assert len(directions) == len(vertices1), f"Number of directions {len(directions)} is different from number of vertices {len(vertices1)}"
    print("end list_intersections")
    return directions, np.array(distances)


def winding_difference(vertex1, angle1, vertex2, angle2, default_splitter):
    # Calculate the angle between two vertices
    angle_dif = default_splitter.angle_between_vertices(vertex1[[2, 0, 1]] - 500, vertex2[[2, 0, 1]] - 500)
    angle_vertex1_on_vertex2 = angle1 + angle_dif
    winding_difference = angle2 - angle_vertex1_on_vertex2
    k = winding_difference / 360.0
    k = np.round(k)
    return k

def find_valid_relations(points2, scene1, triangles1, winding_angles2, max_distance):
    # Find closest vertices
    closest_triangle, closest_distances = find_closest_triangles(points2, scene1)
    # Get the index of the first vertex in the closest triangle
    closest_indices1 = triangles1[closest_triangle][:, 0]

    # Only keep points that are close enough to the mesh
    mask_distance = closest_distances < max_distance
    percentage_valid = 1.0 * np.sum(mask_distance) / len(mask_distance)
    points_indices2 = np.arange(len(points2))[mask_distance]
    closest_indices1 = closest_indices1[mask_distance]
    clostest_points2 = points2[mask_distance]
    # Find the winding angle for each point
    winding_angles_points2 = winding_angles2[mask_distance]

    return closest_indices1, winding_angles_points2, clostest_points2, points_indices2, percentage_valid

def align_winding_angles(vertices1, winding_angles1, mesh2_stuff, umbilicus_path, max_distance, gt_splitter, debug=False):
    # load the meshes
    mesh2, vertices2, triangles2, scene2 = mesh2_stuff

    # calculate the gt winding angles
    winding_angles2 = calculate_winding_angle(gt_splitter)
    assert len(winding_angles1) == len(vertices1)
    assert len(winding_angles2) == len(vertices2)

    # find the closest vertices
    closest_indices2, winding_angles_vertices1, clostest_vertices1, points_indices1, percentage_valid = find_valid_relations(vertices1, scene2, triangles2, winding_angles1, max_distance) # find the clostest triangle of the gt mesh (mesh 2) for each vertex of the input mesh (mesh 1) -> then find the winding angle of one of the vertices of the closest triangle each in the gt mesh (mesh 2)

    # Make statistic about the winding differences in the closest vertices
    winding_angle_difference = {}
    for i in range(len(closest_indices2)):
        vertex2 = vertices2[closest_indices2[i]]
        angle2 = winding_angles2[closest_indices2[i]]
        vertex1 = clostest_vertices1[i]
        angle1 = winding_angles_vertices1[i]
        w_a_diff = winding_difference(vertex2, angle2, vertex1, angle1, gt_splitter)
        if not int(w_a_diff) in winding_angle_difference:
            winding_angle_difference[int(w_a_diff)] = 0
        winding_angle_difference[int(w_a_diff)] += 1

    if debug:
        print(winding_angle_difference)
    return winding_angle_difference, winding_angles2, scene2, mesh2, percentage_valid

def find_best_alignment(winding_angle_difference):
    # find the max used k value
    max_k = max(winding_angle_difference, key=winding_angle_difference.get)
    return 360.0*max_k

def calculate_vertices_error(vertices1, winding_angles1, winding_angles2, triangles2, scene2, default_splitter, distance_threshold=100):
    # Mask the vertices that are optimizable
    optimizable_vertices1 = vertices1

    # Dont forget to mask the corresponding winding angles too
    optimizeable_winding_angles1 = winding_angles1

    # Find distance + direction of mesh1 wrt gt mesh2
    directions1, distances1 = find_closest_triangles_same_winding(optimizable_vertices1, optimizeable_winding_angles1, winding_angles2, triangles2, scene2, default_splitter) # winding_angles2 is index from triangles -> complete vertices, no masking ther on the second winding angles of theis functions

    # Calculate stats from closest same winding triangles
    overlap_mask = distances1 < distance_threshold
    
    # TODO: Calculate overlapping area
    
    overlap_percentage = np.sum(overlap_mask) / overlap_mask.shape[0] # MVP, but wrong way around
    # TODO: actually calculate how much of mesh2 is in mesh 1. not the other way around. 
    
    overlapping_distances = distances1[overlap_mask]
    overlapping_directions = directions1[overlap_mask]
    
    signed_distances = overlapping_directions * overlapping_distances
    mean_distance = np.mean(signed_distances)
    std_distance = signed_distances - mean_distance
    std_distance = np.sqrt(std_distance * std_distance)
    std_distance = np.mean(std_distance)
    
    # Output Mesh Quality stats:
    print(f"The Mesh Quality is:")
    print(f"{100 * overlap_percentage:3}% of the vertices of the Input Mesh is overlapping with the Ground Truth Mesh.")
    print(f"The mean distance of the overlapping Input Mesh vertices to the Ground Truth Mesh is {mean_distance}")
    print(f"The standard deviation of the overlapping Input Mesh vertice distance to the Ground Truth Mesh is {std_distance}")

def calculate_overlapping(secondary_vertices, winding_angles_secondary, winding_angles_primary, triangles_primary, scene_primary, default_splitter):
    optimizable_direction1, optimizable_distance1 = find_closest_triangles_same_winding(secondary_vertices, winding_angles_secondary, winding_angles_primary, triangles_primary, scene_primary, default_splitter)
    overlapping_vertices_secondary_mask = optimizable_distance1 < 10
    return overlapping_vertices_secondary_mask

def mesh_quality(mesh_path1, mesh_path2, umbilicus_path, max_distance, output_dir, distance_threshold=100):
    print(f"Calculating the quality of mesh {mesh_path1} with respect to ground truth mesh {mesh_path2} ...")
    mesh1_splitter = MeshSplitter(mesh_path1, umbilicus_path)
    mesh1_splitter.compute_uv_with_bfs(0) # precomputation of the winding angles of the mesh
    gt_splitter = MeshSplitter(mesh_path2, umbilicus_path)
    gt_splitter.compute_uv_with_bfs(0) # precomputation of the winding angles of the gt mesh

    fresh_start = True
    if fresh_start:
        mesh1, vertices1, _, _ = load_mesh_vertices(mesh_path1)
        winding_angles1 = calculate_winding_angle(mesh_path1, mesh1_splitter)
        mesh2_stuff = load_mesh_vertices(mesh_path2)
        winding_angle_difference, winding_angles2, scene2, mesh2, percentage_valid = align_winding_angles(vertices1, winding_angles1, mesh2_stuff, umbilicus_path, max_distance, gt_splitter)
        best_alignment = find_best_alignment(winding_angle_difference)
        print(f"Best alignment: {best_alignment}")

        # pickle save 
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, "development.pkl"), "wb") as f:
            pickle.dump((winding_angle_difference, winding_angles1, winding_angles2, best_alignment), f)
    else:
        with open(os.path.join(output_dir, "development.pkl"), "rb") as f:
            winding_angle_difference, winding_angles1, winding_angles2, best_alignment = pickle.load(f)

            mesh1, _, _, _ = load_mesh_vertices(mesh_path1)
            mesh2, _, _, scene2 = load_mesh_vertices(mesh_path2)

    # Adjust the winding angles of the second mesh to have the same winding angle base as the first mesh
    print(f"Best alignment: {best_alignment}")
    winding_angles2 -= best_alignment

    print("Mesh 1 min max winding angle: ", np.min(winding_angles1), np.max(winding_angles1))
    print("Mesh 2 min max winding angle: ", np.min(winding_angles2), np.max(winding_angles2))

    primary_vertices = np.asarray(mesh1.vertices)
    secondary_triangles = np.asarray(mesh2.triangles)
    # Iteratively refine vertices positions of both meshes in the overlapping region
    calculate_vertices_error(primary_vertices, winding_angles1, winding_angles2, secondary_triangles, scene2, mesh1_splitter, distance_threshold=distance_threshold)

def compute(input_mesh, input_raw_pointcloud, input_instance_pointcloud, mesh_path2, umbilicus_path, max_distance, distance_threshold):
    output_dir = os.path.dirname(mesh_path2) + "_quality"

    fresh_start = True
    gt_splitter = MeshSplitter(mesh_path2, umbilicus_path)
    if fresh_start:
        gt_splitter.compute_uv_with_bfs(0) # precomputation of the winding angles of the gt mesh
        # make dirs
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, "development.pkl"), "wb") as f:
            pickle.dump(gt_splitter.vertices_np, f)
    else:
        with open(os.path.join(output_dir, "development.pkl"), "rb") as f:
            gt_splitter.vertices_np = pickle.load(f)

    # load input data, get winding angles 1
    axis_indices = [2, 0, 1]
    if input_instance_pointcloud is not None: # TODO proper check for instance
        # 3D instance pointcloud
        vertices1 = o3d.io.read_point_cloud(input_instance_pointcloud)
        # to numpy
        vertices1 = np.asarray(vertices1.points)
        # adjust coordinate frame
        vertices1 = scale_points(vertices1, 4.0, axis_offset=-500)
        vertices1, _ = shuffling_points_axis(vertices1, vertices1, axis_indices)
        winding_angles1 = calculate_winding_angle_pointcloud_instance(vertices1, gt_splitter)
    elif input_raw_pointcloud is not None:
        # 3D raw pointcloud
        vertices1 = o3d.io.read_point_cloud(input_raw_pointcloud)
        # to numpy
        vertices1 = np.asarray(vertices1.points)
        # adjust coordinate frame
        vertices1 = scale_points(vertices1, 1.0, axis_offset=-500)
        vertices1, _ = shuffling_points_axis(vertices1, vertices1, axis_indices)
        # find closest triangles
        mesh2, vertices2, triangles2, scene2 = load_mesh_vertices(mesh_path2)
        closest_triangle, closest_distances = find_closest_triangles(vertices1, scene2)
        # Get the index of the first vertex in the closest triangle
        closest_indices2 = triangles2[closest_triangle][:, 0]
        winding_angles2 = calculate_winding_angle(gt_splitter)
        # Get the winding angles of the closest vertices as the winding angles of the points
        winding_angles1 = winding_angles2[closest_indices2]
    else:
        # mesh
        mesh1, vertices1, _, _ = load_mesh_vertices(input_mesh)
        mesh1_splitter = MeshSplitter(input_mesh, umbilicus_path)
        mesh1_splitter.compute_uv_with_bfs(0) # precomputation of the winding angles of the mesh
        winding_angles1 = calculate_winding_angle(mesh1_splitter)

    # align winding angles
    mesh2_stuff = load_mesh_vertices(mesh_path2)
    winding_angle_difference, winding_angles2, scene2, mesh2, percentage_valid = align_winding_angles(vertices1, winding_angles1, mesh2_stuff, umbilicus_path, max_distance, gt_splitter)
    best_alignment = find_best_alignment(winding_angle_difference)
    print(f"Best alignment: {best_alignment}")

    # Adjust the winding angles of the second mesh to have the same winding angle base as the first mesh
    winding_angles2 += best_alignment

    print("Mesh 1 min max winding angle: ", np.min(winding_angles1), np.max(winding_angles1))
    print("Mesh 2 min max winding angle: ", np.min(winding_angles2), np.max(winding_angles2))

    secondary_triangles = np.asarray(mesh2.triangles)
    # Iteratively refine vertices positions of both meshes in the overlapping region
    calculate_vertices_error(vertices1, winding_angles1, winding_angles2, secondary_triangles, scene2, gt_splitter, distance_threshold=distance_threshold)

    # mesh_quality(mesh_path1, mesh_path2, umbilicus_path, max_distance, output_dir)

def main():
    # Parse arguments
    parser = argparse.ArgumentParser(description="Calculate the Quality statistic of a 3D mesh and a Ground Truth 3D Mesh.")
    parser.add_argument("--input_mesh", type=str, help="Path to the 3D mesh input data (.obj)", default=None)
    parser.add_argument("--input_raw_pointcloud", type=str, help="Path to the 3D raw pointcloud input data (.ply)", default=None)
    parser.add_argument("--input_instance_pointcloud", type=str, help="Path to the 3D raw pointcloud input data (.ply)", default=None)
    parser.add_argument("--gt_mesh", type=str, help="Path to the 3D input mesh (.obj)")
    parser.add_argument("--umbilicus_path", type=str, help="Path to the 3D point cloud directory (containing .ply)")
    parser.add_argument("--max_distance", type=float, help="Maximum distance for a point to be considered part of the mesh", default=1)
    parser.add_argument("--distance_threshold", type=float, help="Distance threshold for overlapping vertices", default=100)

    args = parser.parse_args()
    print(f"args: {args}")

    # Exactly one input must be not None
    assert int(args.input_mesh is not None) + int(args.input_raw_pointcloud is not None) + int(args.input_instance_pointcloud is not None) == 1, "Exactly one input must be not None"

    # Compute the 3D mask with labels
    compute(args.input_mesh, args.input_raw_pointcloud, args.input_instance_pointcloud, args.gt_mesh, args.umbilicus_path, args.max_distance, args.distance_threshold)

if __name__ == "__main__":
    main()

# Example: python3 -m ThaumatoAnakalyptor.mesh_quality --input_mesh thaumato.obj --gt_mesh /scroll.volpkg/merging_test/GP2023.obj --umbilicus_path /scroll.volpkg/merging_test/umbilicus.txt