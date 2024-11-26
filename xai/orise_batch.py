import torch
from .rise import RISE
from tqdm import tqdm

class ORISEBatch(RISE):
    def __init__(self, model, input_size, device ='cpu', N=1000, p1=0.1, gpu_batch=100):
        super(ORISEBatch, self).__init__(model, input_size, device, N, p1, gpu_batch)
        
    def process_in_batches(self, stack):
        """
        Process the masked images in batches and handle variable detection outputs.
        Collects results in a list to accommodate varying numbers of detections.
        """
        results = []  # Initialize an empty list to hold the detection results
        with torch.no_grad():  # Ensure no gradients are computed
            for i in range(0, stack.size(0), self.gpu_batch):
                # Process the batch through the model
                # batch_results = self.model(stack[i:min(i + self.gpu_batch, stack.size(0))])  
                batch_results = self.model(stack,verbose=False)  
                # Instead of concatenating, append each batch's results to the list
                results.extend(batch_results)
        return results
    
    def apply_masks_in_batches(self, x, masks_chunk):
        """
        Apply the generated masks to the input image in chunks.
        """
        stack = torch.mul(masks_chunk, x)  # Apply masks
        return stack

    def calculate_contributions(self, p, masks_chunk, target_class_index, contributions, aggregation):
        """
        Calculate the contributions for the saliency map based on IoU and class scores.
        """
        
        for i in range(len(p)):  # Iterate through each processed output corresponding to a mask
            boxes = p[i].boxes  # Assuming p[i] has 'boxes'
            
            max_contribution = None
            max_points = 0
            mask_contributions = []
            
            for box in boxes:
                score = float(box.conf.item())
                label = int(box.cls.item())
                
                if label == target_class_index:
                    points = score
                    current_contribution = masks_chunk[i, 0, :, :].cpu() * points
                    
                    # monitor max contrib
                    if max_contribution is None or points > max_points:
                        max_points = points
                        max_contribution = current_contribution
                    
                    # append mask contrib
                    mask_contributions.append(current_contribution)
            
            # Add the contribution of the mask
            if aggregation == 'max' and max_contribution is not None:
                contributions.append(max_contribution)
            elif aggregation == 'avg' and mask_contributions:
                avg_contribution = torch.mean(torch.stack(mask_contributions), dim=0)
                contributions.append(avg_contribution)
            elif aggregation == 'sum' and mask_contributions:
                summed_contribution = torch.sum(torch.stack(mask_contributions), dim=0)
                contributions.append(summed_contribution)
                    
        return contributions


    def aggregate_contributions(self, contributions, max_contribution, H, W, aggregation='sum'):
        """
        Aggregate the contributions to form the final saliency map.
        
        Normalizes the output
        """
        saliency_map = torch.zeros((H, W), device='cpu') # contributions increases too much; better to handle at RAM

        # Apply selected aggregation method
        if contributions:
            if aggregation == 'sum':
                for contribution in contributions:
                    saliency_map += contribution
            elif aggregation == 'avg':
                for contribution in contributions:
                    saliency_map += contribution
                saliency_map /= len(contributions)
            # elif aggregation == 'max' and max_contribution is not None:
            #     saliency_map += max_contribution
        
            # ***Normalization
            if saliency_map.max!=0:
                saliency_map /= saliency_map.max()
        
        else:
            print("Warning: No contributions provided for aggregation.")
        
        return saliency_map.cpu().numpy()

    def forward(self, x, target_class_indices, target_bbox):
        """
        Forward pass adapted for object detection to generate saliency maps for multiple classes.
        
        Parameters:
        - x: Input image tensor.
        - target_class_indices: A list of class indices for which to generate saliency maps.
        
        Returns:
        A dictionary of saliency maps, keyed by the target class indices.
        """
    
        _, _, H, W = x.size()
        
        saliency_maps = {}
        contributions = []  # To keep track of all contributions for aggregation
        chunk_size = self.gpu_batch
        
        
        # ***PROCESS EVERYTHING IN CHUCKS***
        for i in tqdm(range(0, self.N, chunk_size)):
            # Split masks
            masks_chunk = self.masks[i:min(i + chunk_size, self.N)].to(self.device)  # Load only the necessary chunk to GPU
            # Apply masks to the image for the current chunk
            stack = self.apply_masks_in_batches(x, masks_chunk)
            p = self.process_in_batches(stack)
                        
            for target_class_index in target_class_indices:
                contributions = self.calculate_contributions(
                    p=p,
                    masks_chunk=masks_chunk, 
                    target_class_index=target_class_index, 
                    contributions=contributions, 
                    aggregation='avg', # ORISE considers the average
                )

            
        # Calculate aggregated & normalized saliency maps
        for target_class_index in target_class_indices:
            saliency_maps[target_class_index] = self.aggregate_contributions(
                contributions=contributions,
                max_contribution=None,
                H=H,W=W,
                aggregation='sum' #sum
            )
        
        return saliency_maps



    # def calculate_contributions(self, p, masks_chunk, target_class_index, target_bbox, contributions, max_contribution, max_points):
    #     """
    #     Calculate the contributions for the saliency map based on IoU and class scores.
    #     """
        
    #     for i in range(len(p)):  # Iterate through each processed output corresponding to a mask
    #         boxes = p[i].boxes  # Assuming p[i] has 'boxes'
    #         mask_contribution = [] 
            
    #         for box in boxes:
    #             score = float(box.conf.item())
    #             label = int(box.cls.item())
                
    #             if label == target_class_index:
    #                 bbox = box.xyxy[0].cpu().numpy()  # Access bounding box coordinates, assuming xyxy format (x1,y1 top left; x2,y2 bottom right)
    #                 x1, y1, x2, y2 = bbox
    #                 top_left_corner = (x1, y1)
    #                 top_right_corner = (x2, y1)
    #                 bottom_right_corner = (x2, y2)
    #                 bottom_left_corner = (x1, y2)
    #                 vertices = [top_left_corner, top_right_corner, bottom_right_corner, bottom_left_corner]

    #                 # Calculate IoU
    #                 iou_score = self.calculate_iou(polygon1=target_bbox, polygon2=vertices)
                    
    #                 if iou_score > 0:  # Only consider overlaps
    #                     points = iou_score * score
    #                     current_contribution = masks_chunk[i, 0, :, :].cpu() * points
    #                     # mask_contribution = masks_chunk[i, 0, :, :].cpu() * points
                        
    #                     if current_contribution is None:
    #                         mask_contribution = current_contribution
    #                     else:
                        
    #                     contributions.append(mask_contribution)

    #                     if points > max_points:
    #                         max_points = points
    #                         max_contribution = mask_contribution
            
    #         # AGGREGATE CONTRIBUTIONS HAS TO BE HERE (select sum, avg or max among the proposals for EACH MASK!) --> CHECK IF NORMALIZE OR NOT HERE (I THINK HERE IS NOT NECESSARY)
        
    #     return contributions, max_contribution, max_points